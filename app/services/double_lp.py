"""Double LP (« Aegis of Valor ») : repérage et retrait du bonus des LP nets.

Depuis 2026, Riot double parfois les LP d'une victoire en Solo/Duo (« Aegis of Valor » : joueur
placé en autofill, ou parfois support / jungle sans prévenir). L'API Riot ne dit pas quand :
Match-V5 n'a aucun champ pour ça et League-V4 ne donne que le total de LP. Le site le repère
donc aux LP : une victoire qui rapporte environ le double du gain habituel du joueur.

- Gain habituel : médiane des autres victoires du joueur dans la même file (LP connus, hors
  double LP). Il en faut au moins `REFERENCE_MIN_WINS`.
- Double LP si le gain atteint `DOUBLE_RATIO` × le gain habituel (et au moins `DOUBLE_MIN_GAIN`).
  Sans gain habituel connu, seul un gain d'au moins `NO_REFERENCE_MIN_GAIN` LP est retenu ; une
  victoire plus modeste reste « à revoir » et sera jugée quand le joueur aura d'autres victoires.
- Bonus retiré des LP nets : la moitié du gain (arrondie à l'inférieur), le reste compte.
- L'organisateur peut corriger dans l'Admin (`double_lp_manual`) : sa décision n'est plus revue.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable

from sqlmodel import Session, col, select

from app.db.models import MatchParticipant, Player, Queue, game_end_of

REFERENCE_MIN_WINS = 2
# Un double LP exact vaut 2 × le gain habituel ; le gain varie d'une partie à l'autre de quelques LP
DOUBLE_RATIO = 1.6
DOUBLE_MIN_GAIN = 30
NO_REFERENCE_MIN_GAIN = 40


def lp_bonus(p: MatchParticipant) -> int:
    """LP bonus d'une partie à double LP (retirés des LP nets) ; 0 sinon."""
    if not p.double_lp or p.lp_change is None or p.lp_change <= 0:
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
    if lp_change is None or lp_change <= 0:
        return None
    if reference is None:
        return True if lp_change >= NO_REFERENCE_MIN_GAIN else None
    return lp_change >= max(DOUBLE_MIN_GAIN, DOUBLE_RATIO * reference)


def _queue(value: Queue | str | None) -> Queue:
    return value if isinstance(value, Queue) else Queue(value or Queue.SOLO.value)


def assess_double_lp(session: Session, player: Player) -> list[tuple[MatchParticipant, float | None]]:
    """Juge les victoires du joueur dont les LP sont connus et pas encore jugées.

    Renvoie les parties nouvellement repérées comme double LP, avec le gain habituel du joueur
    (None si repérée sans référence), dans l'ordre chronologique. Les décisions de
    l'organisateur (`double_lp_manual`) ne sont jamais revues.
    """
    if player.id is None:
        return []
    wins = session.exec(
        select(MatchParticipant).where(
            MatchParticipant.player_id == player.id,
            MatchParticipant.is_remake == False,  # noqa: E712
            MatchParticipant.win == True,  # noqa: E712
            col(MatchParticipant.lp_change).is_not(None),
        )
    ).all()
    wins = sorted(wins, key=game_end_of)
    flagged: list[tuple[MatchParticipant, float | None]] = []
    changed = False
    for game in wins:
        if game.double_lp is not None or game.double_lp_manual:
            continue
        references = [
            int(other.lp_change)  # type: ignore[arg-type]
            for other in wins
            if other.id != game.id and _queue(other.queue) == _queue(game.queue) and other.double_lp is not True
        ]
        reference = usual_gain(references)
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
