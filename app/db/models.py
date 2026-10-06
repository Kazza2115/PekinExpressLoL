"""Modèle de données (SQLModel / SQLite).

Toutes les dates sont stockées en UTC (datetime *aware*). Le découpage par
journée (10 games/jour) se fait côté stats avec le fuseau `Settings.timezone`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChallengeStatus(str, Enum):
    REGISTRATION = "registration"  # les joueurs s'inscrivent et lient leur compte
    DRAWN = "drawn"  # les duos ont été tirés, le challenge n'a pas commencé
    RUNNING = "running"  # challenge en cours (polling + classement)
    FINISHED = "finished"  # challenge terminé (classement figé)


class Queue(str, Enum):
    SOLO = "SOLO"
    FLEX = "FLEX"


# Correspondance file classée Riot ↔ queueId Match-V5 ↔ queueType League-V4
QUEUE_IDS = {Queue.SOLO: 420, Queue.FLEX: 440}
QUEUE_TYPES = {Queue.SOLO: "RANKED_SOLO_5x5", Queue.FLEX: "RANKED_FLEX_SR"}
QUEUE_BY_ID = {v: k for k, v in QUEUE_IDS.items()}
QUEUE_BY_TYPE = {v: k for k, v in QUEUE_TYPES.items()}

# Une partie plus courte que ça est un remake → exclue des stats
REMAKE_MAX_DURATION_S = 5 * 60


class Challenge(SQLModel, table=True):
    """Il n'y a qu'une ligne : la configuration du challenge en cours."""

    id: int | None = Field(default=None, primary_key=True)
    name: str = "Pékin Express LoL"
    status: ChallengeStatus = Field(default=ChallengeStatus.REGISTRATION)
    start_at: datetime | None = None  # début effectif (clic "Démarrer")
    end_at: datetime | None = None  # fin (clic "Terminer") ou None
    games_per_day: int = 10
    track_flex: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class Team(SQLModel, table=True):
    """Un duo. `slot` = ordre de tirage (1..n). Fenêtre optionnelle si chaque duo a son propre week-end."""

    id: int | None = Field(default=None, primary_key=True)
    name: str
    color: str  # couleur hex, ex. "#ef4444"
    slot: int
    window_start: datetime | None = None  # None → fenêtre du challenge
    window_end: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Player(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    display_name: str = Field(index=True)  # surnom pour le challenge
    game_name: str | None = None  # Riot ID : partie gauche
    tag_line: str | None = None  # Riot ID : partie droite (sans #)
    puuid: str | None = Field(default=None, unique=True, index=True)
    summoner_id: str | None = None
    profile_icon_id: int | None = None
    summoner_level: int | None = None
    team_id: int | None = Field(default=None, foreign_key="team.id", index=True)
    active: bool = True
    linked_at: datetime | None = None  # None → compte LoL non lié
    link_error: str | None = None  # dernier message d'erreur de liaison (affiché à l'utilisateur)
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def riot_id(self) -> str | None:
        if self.game_name and self.tag_line:
            return f"{self.game_name}#{self.tag_line}"
        return None

    @property
    def is_linked(self) -> bool:
        return bool(self.puuid)


class RankSnapshot(SQLModel, table=True):
    """Photo du rang à un instant t. Insérée à chaque poll où quelque chose change."""

    id: int | None = Field(default=None, primary_key=True)
    player_id: int = Field(foreign_key="player.id", index=True)
    queue: Queue = Field(default=Queue.SOLO)
    tier: str | None = None  # "GOLD", "MASTER"… ; None → unranked
    rank: str | None = None  # "IV".."I" ; None pour Master+ / unranked
    lp: int = 0
    wins: int = 0
    losses: int = 0
    hot_streak: bool = False
    absolute_lp: int | None = None  # valeur absolue (stats.absolute_lp) ; None si unranked
    captured_at: datetime = Field(default_factory=utcnow, index=True)


class Match(SQLModel, table=True):
    """Cache brut d'une partie (ne change jamais une fois terminée)."""

    match_id: str = Field(primary_key=True)
    queue_id: int
    game_start: datetime = Field(index=True)
    game_duration: int  # secondes
    raw_json: str  # JSON Match-V5 complet
    fetched_at: datetime = Field(default_factory=utcnow)


class MatchParticipant(SQLModel, table=True):
    """Ligne d'un joueur du challenge dans une partie."""

    id: int | None = Field(default=None, primary_key=True)
    match_id: str = Field(foreign_key="match.match_id", index=True)
    player_id: int = Field(foreign_key="player.id", index=True)
    queue: Queue = Field(default=Queue.SOLO)
    game_start: datetime = Field(index=True)  # dénormalisé (tri / fenêtre)
    game_duration: int
    is_remake: bool = False
    champion_name: str
    champion_id: int | None = None
    position: str | None = None  # TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY
    win: bool
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    cs: int = 0
    gold: int = 0
    damage_to_champions: int = 0
    vision_score: int = 0
    lp_change: int | None = None  # calculé par diff de snapshots (approximation)
