"""Double LP (« Aegis of Valor ») : repérage et retrait du bonus des LP nets.

Depuis 2026, Riot double parfois les LP d'une victoire en Solo/Duo (« Aegis of Valor » : joueur
placé en autofill, ou parfois support / jungle sans prévenir). L'API Riot ne dit pas quand :
Match-V5 n'a aucun champ pour ça et League-V4 ne donne que le total de LP. Le site le repère
donc aux LP : une victoire qui rapporte environ le double du gain habituel du joueur.

- Seule la file Solo/Duo est jugée : c'est la seule qui compte pour les LP nets.
- Gain habituel : médiane des victoires de référence les plus proches dans le temps (le gain
  évolue pendant la montée). Les références sont les autres victoires enregistrées, et aussi
  chaque victoire lue dans les relevés de rang (+1 victoire, LP en hausse), y compris avant le
  début du challenge si le site tournait déjà. Il en faut au moins `REFERENCE_MIN_WINS` : sans
  ça, la victoire reste « à revoir ». Jamais de double LP sans comparaison (une victoire juste
  après les placements, ou d'un compte au MMR élevé, rapporte normalement 40 LP ou plus).
- Double LP si le gain atteint `DOUBLE_RATIO` × le gain habituel (et au moins `DOUBLE_MIN_GAIN`).
- Une victoire jugée normale est rejugée à chaque cycle (de nouvelles références peuvent montrer
  que c'était un double LP) ; un double LP repéré reste acquis (seul l'organisateur l'annule).
- Une victoire dont les relevés de rang encadrent plusieurs parties Riot (compteurs victoires +
  défaites) n'est ni jugée ni prise comme référence : ses LP mélangent plusieurs parties.
- Bonus retiré des LP nets : la moitié du gain (arrondie à l'inférieur), le reste compte.
- L'organisateur peut corriger dans l'Admin (`double_lp_manual`) : sa décision n'est plus revue.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, col, select

from app.db.models import MatchParticipant, Player, Queue, RankSnapshot, game_end_of
from app.db.session import as_utc

REFERENCE_MIN_WINS = 3
# Victoires de référence retenues : les plus proches dans le temps de la victoire jugée
REFERENCE_NEAREST = 6
# Un double LP exact vaut 2 × le gain habituel ; le gain varie d'une partie à l'autre de quelques LP
DOUBLE_RATIO = 1.6
# En dessous, jamais de double LP (le double d'un petit gain de 12 à 14 LP reste ambigu)
DOUBLE_MIN_GAIN = 24


def _is_solo(value: Queue | str | None) -> bool:
    return (value.value if isinstance(value, Queue) else (value or Queue.SOLO.value)) == Queue.SOLO.value


def lp_bonus(p: MatchParticipant) -> int:
    """LP bonus d'une partie à double LP (retirés des LP nets) ; 0 sinon (et hors Solo/Duo)."""
    if not p.double_lp or p.lp_change is None or p.lp_change <= 0 or not _is_solo(p.queue):
        return 0
    return int(p.lp_change) // 2


def counted_lp(p: MatchParticipant) -> int | None:
    """LP de la partie qui comptent pour le challenge (bonus de double LP retiré) ; None si inconnus."""
    if p.lp_change is None:
        return None
    return int(p.lp_change) - lp_bonus(p)


def usual_gain(gains: Iterable[int]) -> float | None:
    """Gain habituel : médiane des victoires de référence ; None s'il y en a trop peu."""
    values = [int(g) for g in gains if g is not None and g > 0]
    if len(values) < REFERENCE_MIN_WINS:
        return None
    return float(statistics.median(values))


def threshold_for(reference: float | None) -> int | None:
    """Gain minimal d'un double LP pour ce gain habituel ; None sans gain habituel."""
    if reference is None:
        return None
    return max(DOUBLE_MIN_GAIN, math.ceil(DOUBLE_RATIO * reference - 1e-9))


def judge(lp_change: int | None, reference: float | None) -> bool | None:
    """True = double LP, False = victoire normale, None = pas encore jugeable (pas de gain habituel)."""
    threshold = threshold_for(reference)
    if lp_change is None or lp_change <= 0 or threshold is None:
        return None
    return lp_change >= threshold


def _riot_games(snapshot: RankSnapshot) -> int:
    return int(snapshot.wins or 0) + int(snapshot.losses or 0)


def single_game_interval(game: MatchParticipant, snapshots: Sequence[RankSnapshot]) -> bool:
    """Les relevés qui encadrent la partie couvrent au plus une partie Riot (LP non mélangés).

    `snapshots` : relevés Solo/Duo du joueur, triés par date. Sans relevé avant ou après, on ne
    peut rien dire : True (de toute façon, `lp_change` n'est alors pas connu).
    """
    before, after = _bracket(game, snapshots)
    if before is None or after is None:
        return True
    return _riot_games(after) - _riot_games(before) <= 1


def _bracket(game: MatchParticipant, snapshots: Sequence[RankSnapshot]) -> tuple[RankSnapshot | None, RankSnapshot | None]:
    end = game_end_of(game)
    before: RankSnapshot | None = None
    for snapshot in snapshots:
        captured = as_utc(snapshot.captured_at)
        if captured is not None and captured <= end:
            before = snapshot
        else:
            return before, snapshot
    return before, None


@dataclass(frozen=True)
class Assessment:
    """Ce que la détection voit pour une victoire (affiché dans l'Admin)."""

    usual: float | None  # gain habituel (None : moins de `REFERENCE_MIN_WINS` références)
    threshold: int | None  # gain minimal d'un double LP
    references: int  # victoires de référence disponibles
    mixed: bool  # LP mélangés avec une autre partie Riot : non jugée


class _History:
    """Victoires Solo/Duo aux LP connus et relevés de rang Solo/Duo d'un joueur."""

    def __init__(self, wins: list[MatchParticipant], snapshots: list[RankSnapshot]) -> None:
        self.wins = wins
        self.snapshots = snapshots
        self._keys = {w.id: self._key(w) for w in wins}
        self._mixed = {w.id for w in wins if not single_game_interval(w, snapshots)}
        # Gains de victoires, une entrée par intervalle entre deux relevés : (fin, gain, partie)
        self._points: dict[object, tuple[datetime, int, MatchParticipant | None]] = {}
        for win in wins:
            if win.id not in self._mixed:
                self._points[self._keys[win.id]] = (game_end_of(win), int(win.lp_change or 0), win)
        for before, after in zip(snapshots, snapshots[1:]):
            if before.absolute_lp is None or after.absolute_lp is None or after.id in self._points:
                continue
            one_win = int(after.wins or 0) - int(before.wins or 0) == 1 and int(after.losses or 0) == int(before.losses or 0)
            gain = int(after.absolute_lp) - int(before.absolute_lp)
            captured = as_utc(after.captured_at)
            if one_win and gain > 0 and captured is not None:
                self._points[after.id] = (captured, gain, None)

    @classmethod
    def load(cls, session: Session, player_id: int) -> _History:
        return cls(_solo_wins(session, player_id), _solo_snapshots(session, player_id))

    def _key(self, game: MatchParticipant) -> object:
        """Intervalle de relevés de la partie : l'identifiant du relevé qui suit sa fin."""
        _before, after = _bracket(game, self.snapshots)
        return after.id if after is not None else ("partie", game.id)

    def mixed(self, game: MatchParticipant) -> bool:
        return game.id in self._mixed

    def assess(self, game: MatchParticipant) -> Assessment:
        own = self._keys.get(game.id)
        flagged = {self._keys[w.id] for w in self.wins if w.double_lp is True and w.id != game.id}
        end = game_end_of(game)
        points = [
            (abs((when - end).total_seconds()), gain)
            for key, (when, gain, _win) in self._points.items()
            if key != own and key not in flagged
        ]
        gains = [gain for _distance, gain in sorted(points)[:REFERENCE_NEAREST]]
        usual = usual_gain(gains)
        return Assessment(usual=usual, threshold=threshold_for(usual), references=len(points), mixed=self.mixed(game))


def _solo_wins(session: Session, player_id: int) -> list[MatchParticipant]:
    wins = session.exec(
        select(MatchParticipant).where(
            MatchParticipant.player_id == player_id,
            MatchParticipant.is_remake == False,  # noqa: E712
            MatchParticipant.win == True,  # noqa: E712
            col(MatchParticipant.lp_change).is_not(None),
            col(MatchParticipant.lp_change) > 0,
        )
    ).all()
    return sorted((w for w in wins if _is_solo(w.queue)), key=game_end_of)


def _solo_snapshots(session: Session, player_id: int) -> list[RankSnapshot]:
    snapshots = session.exec(
        select(RankSnapshot)
        .where(RankSnapshot.player_id == player_id)
        .order_by(col(RankSnapshot.captured_at), col(RankSnapshot.id))
    ).all()
    return [s for s in snapshots if _is_solo(s.queue)]


def explain(session: Session, player_id: int) -> dict[int, Assessment]:
    """Ce que la détection voit pour chaque victoire Solo/Duo du joueur (Admin)."""
    history = _History.load(session, player_id)
    return {w.id: history.assess(w) for w in history.wins if w.id is not None}


def assess_double_lp(session: Session, player: Player) -> list[tuple[MatchParticipant, float | None]]:
    """Juge les victoires Solo/Duo du joueur dont les LP sont connus : celles pas encore jugées, et
    celles jugées normales (de nouvelles références peuvent révéler un double LP).

    Renvoie les parties nouvellement repérées comme double LP, avec le gain habituel retenu,
    dans l'ordre chronologique. Un double LP repéré n'est jamais retiré automatiquement, et les
    décisions de l'organisateur (`double_lp_manual`) ne sont jamais revues.
    """
    if player.id is None:
        return []
    history = _History.load(session, player.id)
    flagged: list[tuple[MatchParticipant, float | None]] = []
    changed = False
    for game in history.wins:
        if game.double_lp is True or game.double_lp_manual or history.mixed(game):
            continue
        assessment = history.assess(game)
        verdict = judge(game.lp_change, assessment.usual)
        if verdict is None:
            continue
        basis = (assessment.usual, assessment.threshold)
        if verdict == game.double_lp and basis == (game.double_lp_usual, game.double_lp_threshold):
            continue
        # Le gain habituel et le seuil du jugement sont gardés : l'Admin explique ainsi la décision
        # telle qu'elle a été prise, même si les références changent ensuite
        newly_flagged = verdict and not game.double_lp
        game.double_lp = verdict
        game.double_lp_usual, game.double_lp_threshold = basis
        session.add(game)
        changed = True
        if newly_flagged:
            flagged.append((game, assessment.usual))
    if changed:
        session.commit()
    return flagged
