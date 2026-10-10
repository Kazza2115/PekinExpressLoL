"""Double LP (« Aegis of Valor ») : repérage et retrait du bonus des LP nets.

Depuis 2026, Riot double parfois les LP d'une victoire en Solo/Duo (« Aegis of Valor » : joueur
placé en autofill, ou parfois support / jungle sans prévenir). L'API Riot ne dit pas quand :
Match-V5 n'a aucun champ pour ça et League-V4 ne donne que le total de LP. Le site le repère
donc aux LP : une victoire qui rapporte environ le double du gain habituel du joueur.

- Seule la file Solo/Duo est jugée : c'est la seule qui compte pour les LP nets.
- Gain habituel : médiane des victoires de référence les plus proches dans le temps (le gain
  évolue pendant la montée). Il en faut au moins `REFERENCE_MIN_WINS` : sans ça, la victoire
  reste « à revoir ». Jamais de double LP sans comparaison (une victoire juste après les
  placements, ou d'un compte au MMR élevé, rapporte normalement 40 LP ou plus).
- Double LP si le gain atteint `DOUBLE_RATIO` × le gain habituel (et au moins `DOUBLE_MIN_GAIN`).
- Une victoire dont les relevés de rang encadrent plusieurs parties Riot (compteurs victoires +
  défaites) n'est ni jugée ni prise comme référence : ses LP mélangent plusieurs parties.
- Bonus retiré des LP nets : la moitié du gain (arrondie à l'inférieur), le reste compte.
- L'organisateur peut corriger dans l'Admin (`double_lp_manual`) : sa décision n'est plus revue.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence

from sqlmodel import Session, col, select

from app.db.models import MatchParticipant, Player, Queue, RankSnapshot, game_end_of
from app.db.session import as_utc

REFERENCE_MIN_WINS = 3
# Victoires de référence retenues : les plus proches dans le temps de la victoire jugée
REFERENCE_NEAREST = 6
# Un double LP exact vaut 2 × le gain habituel ; le gain varie d'une partie à l'autre de quelques LP
DOUBLE_RATIO = 1.6
DOUBLE_MIN_GAIN = 30


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


def judge(lp_change: int | None, reference: float | None) -> bool | None:
    """True = double LP, False = victoire normale, None = pas encore jugeable (pas de gain habituel)."""
    if lp_change is None or lp_change <= 0 or reference is None:
        return None
    return lp_change >= max(DOUBLE_MIN_GAIN, DOUBLE_RATIO * reference)


def _riot_games(snapshot: RankSnapshot) -> int:
    return int(snapshot.wins or 0) + int(snapshot.losses or 0)


def single_game_interval(game: MatchParticipant, snapshots: Sequence[RankSnapshot]) -> bool:
    """Les relevés qui encadrent la partie couvrent au plus une partie Riot (LP non mélangés).

    `snapshots` : relevés Solo/Duo du joueur, triés par date. Sans relevé avant ou après, on ne
    peut rien dire : True (de toute façon, `lp_change` n'est alors pas connu).
    """
    end = game_end_of(game)
    before: RankSnapshot | None = None
    after: RankSnapshot | None = None
    for snapshot in snapshots:
        captured = as_utc(snapshot.captured_at)
        if captured is not None and captured <= end:
            before = snapshot
        else:
            after = snapshot
            break
    if before is None or after is None:
        return True
    return _riot_games(after) - _riot_games(before) <= 1


def nearest_references(game: MatchParticipant, candidates: Sequence[MatchParticipant]) -> list[int]:
    """LP des victoires de référence les plus proches dans le temps de `game`."""
    end = game_end_of(game)

    def distance(other: MatchParticipant) -> float:
        return abs((game_end_of(other) - end).total_seconds())

    nearest = sorted(candidates, key=distance)[:REFERENCE_NEAREST]
    return [int(other.lp_change) for other in nearest]  # type: ignore[arg-type]


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


def reference_gains(session: Session, player_id: int) -> dict[int, float | None]:
    """Gain habituel autour de chaque victoire Solo/Duo du joueur (même règle que le jugement),
    calculé avec les décisions actuelles : pour l'affichage dans l'Admin."""
    wins = _solo_wins(session, player_id)
    snapshots = _solo_snapshots(session, player_id)
    clean = {w.id for w in wins if single_game_interval(w, snapshots)}
    gains: dict[int, float | None] = {}
    for game in wins:
        if game.id is None:
            continue
        candidates = [w for w in wins if w.id != game.id and w.id in clean and w.double_lp is not True]
        gains[game.id] = usual_gain(nearest_references(game, candidates))
    return gains


def assess_double_lp(session: Session, player: Player) -> list[tuple[MatchParticipant, float | None]]:
    """Juge les victoires Solo/Duo du joueur dont les LP sont connus et pas encore jugées.

    Renvoie les parties nouvellement repérées comme double LP, avec le gain habituel retenu,
    dans l'ordre chronologique. Les décisions de l'organisateur (`double_lp_manual`) ne sont
    jamais revues.
    """
    if player.id is None:
        return []
    wins = _solo_wins(session, player.id)
    if not any(w.double_lp is None and not w.double_lp_manual for w in wins):
        return []
    snapshots = _solo_snapshots(session, player.id)
    clean = {w.id for w in wins if single_game_interval(w, snapshots)}
    flagged: list[tuple[MatchParticipant, float | None]] = []
    changed = False
    for game in wins:
        if game.double_lp is not None or game.double_lp_manual or game.id not in clean:
            continue
        candidates = [w for w in wins if w.id != game.id and w.id in clean and w.double_lp is not True]
        reference = usual_gain(nearest_references(game, candidates))
        verdict = judge(game.lp_change, reference)
        if verdict is None:
            continue
        game.double_lp = verdict
        session.add(game)
        changed = True
        if verdict:
            flagged.append((game, reference))
    if changed:
        session.commit()
    return flagged
