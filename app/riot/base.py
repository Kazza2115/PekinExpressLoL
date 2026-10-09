"""Contrat commun des clients Riot (réel et démo).

Le poller et le service d'inscription ne connaissent que cette interface ;
`app.riot.get_api()` renvoie l'implémentation adaptée (démo ou réelle).
Les parties sont renvoyées au format brut Match-V5 (dict) dans les deux cas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


class RiotError(Exception):
    """Erreur générique de l'API Riot (après retries)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class RiotNotFound(RiotError):
    """404 : compte / joueur / partie inexistant (ou pas en game pour Spectator)."""


class RiotUnauthorized(RiotError):
    """401/403 : clé absente, expirée ou invalide."""


class RiotRateLimited(RiotError):
    """429 persistant malgré les retries."""


class RiotUnreachable(RiotError):
    """API Riot injoignable : erreur réseau ou timeout après retries."""


@dataclass
class AccountDTO:
    puuid: str
    game_name: str
    tag_line: str


@dataclass
class SummonerDTO:
    puuid: str
    summoner_id: str | None
    profile_icon_id: int | None
    summoner_level: int | None


@dataclass
class LeagueEntryDTO:
    queue_type: str  # "RANKED_SOLO_5x5" | "RANKED_FLEX_SR"
    tier: str  # "GOLD"…
    rank: str  # "IV".."I" ("I" pour Master+)
    league_points: int
    wins: int
    losses: int
    hot_streak: bool = False


@dataclass
class ActiveGameDTO:
    game_id: int
    game_start: datetime  # UTC (0 → début non encore connu : utiliser detected_at)
    queue_id: int
    game_mode: str  # "CLASSIC"…
    champion_id: int
    champion_name: str | None = None  # résolu via Data Dragon si None
    # puuid → championId de tous les joueurs de la partie (duo dans la même partie) ; vide si inconnu
    champions_by_puuid: dict[str, int] = field(default_factory=dict)


class RiotAPI(Protocol):
    """Interface asynchrone minimale utilisée par l'application."""

    async def get_account_by_riot_id(self, game_name: str, tag_line: str) -> AccountDTO:
        """Account-V1 (routing régional). Lève RiotNotFound si inconnu."""
        ...

    async def get_summoner_by_puuid(self, puuid: str) -> SummonerDTO:
        """Summoner-V4 (routing plateforme)."""
        ...

    async def get_league_entries_by_puuid(self, puuid: str) -> list[LeagueEntryDTO]:
        """League-V4 : liste vide si unranked."""
        ...

    async def get_match_ids_by_puuid(
        self,
        puuid: str,
        queue_id: int,
        start_time: int | None = None,
        count: int = 20,
        start: int = 0,
    ) -> list[str]:
        """Match-V5 : IDs des dernières parties (les plus récentes d'abord), à partir de l'index `start`."""
        ...

    async def get_match(self, match_id: str) -> dict[str, Any]:
        """Match-V5 : JSON brut complet (metadata + info)."""
        ...

    async def get_active_game(self, puuid: str) -> ActiveGameDTO | None:
        """Spectator-V5 : None si le joueur n'est pas en partie."""
        ...

    async def aclose(self) -> None:
        ...
