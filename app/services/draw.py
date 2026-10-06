"""Tirage au sort des duos (« la roue »).

`draw_pairs` est pur (mélange uniforme) ; `perform_draw` applique le tirage en base :
suppression des anciens duos, création des nouveaux (palette dans l'ordre), affectation
des joueurs, statut → `drawn`, puis événement `draw_done`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.db.models import Challenge, ChallengeStatus, Player, Team
from app.events import bus

# Noms / couleurs des duos dans l'ordre de tirage
TEAM_PALETTE: list[tuple[str, str]] = [
    ("Duo Rouge", "#ef4444"),
    ("Duo Bleu", "#3b82f6"),
    ("Duo Vert", "#22c55e"),
    ("Duo Or", "#f59e0b"),
    ("Duo Violet", "#a855f7"),
    ("Duo Rose", "#ec4899"),
    ("Duo Cyan", "#06b6d4"),
    ("Duo Orange", "#f97316"),
]

EVEN_COUNT_MESSAGE = "Il faut un nombre pair de joueurs (au moins 2)."


def draw_pairs(player_ids: list[int], rng: random.Random | None = None) -> list[tuple[int, int]]:
    """Mélange uniforme des joueurs puis regroupement deux par deux.

    L'ordre de sortie est l'ordre de la roue : paire i = (ids[2i], ids[2i+1]).
    Lève ValueError si le nombre de joueurs est nul ou impair.
    """
    if not player_ids or len(player_ids) % 2 != 0:
        raise ValueError(EVEN_COUNT_MESSAGE)
    rng = rng if rng is not None else random.Random()
    shuffled = list(player_ids)  # copie : la liste d'entrée n'est pas modifiée
    rng.shuffle(shuffled)
    return [(shuffled[i], shuffled[i + 1]) for i in range(0, len(shuffled), 2)]


def team_identity(slot: int) -> tuple[str, str]:
    """Nom et couleur du duo `slot` (1..n) ; au‑delà de la palette : "Duo N" + couleur recyclée."""
    index = slot - 1
    if 0 <= index < len(TEAM_PALETTE):
        return TEAM_PALETTE[index]
    return (f"Duo {slot}", TEAM_PALETTE[index % len(TEAM_PALETTE)][1])


@dataclass
class DrawResult:
    order: list[int]  # IDs des joueurs dans l'ordre de la roue (paire 1 = order[0], order[1]…)
    teams: list[dict] = field(default_factory=list)  # [{id, name, color, slot, player_ids: [a, b]}]

    def to_dict(self) -> dict:
        return {"order": list(self.order), "teams": [dict(t) for t in self.teams]}


def perform_draw(session: Session, *, rng: random.Random | None = None) -> DrawResult:
    """Tire les duos et les enregistre.

    Pré‑conditions (ValueError, message FR sinon) : un challenge en `registration` ou
    `drawn`, tous les joueurs actifs liés à un compte Riot, nombre pair ≥ 2.
    """
    challenge = session.exec(select(Challenge)).first()
    if challenge is None:
        raise ValueError("Aucun challenge n'est configuré.")
    if challenge.status not in (ChallengeStatus.REGISTRATION, ChallengeStatus.DRAWN):
        raise ValueError("Le challenge a déjà démarré.")

    all_players = list(session.exec(select(Player).order_by(Player.id)).all())  # type: ignore[arg-type]
    active_players = [p for p in all_players if p.active]
    unlinked = [p for p in active_players if not p.puuid]
    if unlinked:
        raise ValueError("Comptes non liés : " + ", ".join(p.display_name for p in unlinked))
    if len(active_players) < 2 or len(active_players) % 2 != 0:
        raise ValueError(EVEN_COUNT_MESSAGE)

    players_by_id = {int(p.id): p for p in active_players if p.id is not None}
    pairs = draw_pairs(list(players_by_id), rng)

    # Anciens duos : on détache d'abord les joueurs (clé étrangère) puis on supprime
    for player in all_players:
        if player.team_id is not None:
            player.team_id = None
            session.add(player)
    session.flush()
    for old_team in session.exec(select(Team)).all():
        session.delete(old_team)
    session.flush()

    # Nouveaux duos dans l'ordre de tirage
    created: list[tuple[Team, tuple[int, int]]] = []
    for slot, pair in enumerate(pairs, start=1):
        name, color = team_identity(slot)
        team = Team(name=name, color=color, slot=slot)
        session.add(team)
        session.flush()  # récupère team.id
        for player_id in pair:
            player = players_by_id[player_id]
            player.team_id = team.id
            session.add(player)
        created.append((team, pair))

    challenge.status = ChallengeStatus.DRAWN
    session.add(challenge)
    session.commit()

    teams: list[dict] = []
    for team, pair in created:
        session.refresh(team)
        teams.append(
            {
                "id": team.id,
                "name": team.name,
                "color": team.color,
                "slot": team.slot,
                "player_ids": [pair[0], pair[1]],
            }
        )
    result = DrawResult(order=[player_id for pair in pairs for player_id in pair], teams=teams)
    bus.publish("draw_done", result.to_dict())
    return result
