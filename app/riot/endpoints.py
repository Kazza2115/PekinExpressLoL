"""Constructeurs d'URL et parseurs typés de l'API Riot (utilisés par `RiotClient`).

Aucune logique réseau ici : uniquement la correspondance entre les JSON Riot et
les DTO de `app.riot.base`. Les composants de chemin sont encodés avec
`urllib.parse.quote` (un Riot ID peut contenir des espaces ou des accents).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

from app.riot.base import AccountDTO, ActiveGameDTO, LeagueEntryDTO, SummonerDTO


def _component(value: str | int) -> str:
    """Encode un composant de chemin (y compris `/`)."""
    return quote(str(value), safe="")


# --------------------------------------------------------------------------- #
# URLs
# --------------------------------------------------------------------------- #


def account_url(region_host: str, game_name: str, tag_line: str) -> str:
    """Account-V1 (routing régional) : Riot ID → puuid."""
    return f"{region_host}/riot/account/v1/accounts/by-riot-id/{_component(game_name)}/{_component(tag_line)}"


def summoner_url(platform_host: str, puuid: str) -> str:
    """Summoner-V4 (routing plateforme) : icône / niveau."""
    return f"{platform_host}/lol/summoner/v4/summoners/by-puuid/{_component(puuid)}"


def league_entries_url(platform_host: str, puuid: str) -> str:
    """League-V4 (routing plateforme) : entrées classées du joueur."""
    return f"{platform_host}/lol/league/v4/entries/by-puuid/{_component(puuid)}"


def match_ids_url(
    region_host: str,
    puuid: str,
    queue_id: int | None = None,
    start_time: int | None = None,
    count: int = 20,
    start: int = 0,
) -> str:
    """Match-V5 (routing régional) : IDs des dernières parties, filtrés par file / date."""
    params: dict[str, Any] = {"start": max(0, int(start)), "count": max(1, min(int(count), 100))}
    if queue_id is not None:
        params["queue"] = int(queue_id)
    if start_time is not None:
        params["startTime"] = int(start_time)
    return f"{region_host}/lol/match/v5/matches/by-puuid/{_component(puuid)}/ids?{urlencode(params)}"


def match_url(region_host: str, match_id: str) -> str:
    """Match-V5 (routing régional) : détail d'une partie."""
    return f"{region_host}/lol/match/v5/matches/{_component(match_id)}"


def active_game_url(platform_host: str, puuid: str) -> str:
    """Spectator-V5 (routing plateforme) : partie en cours."""
    return f"{platform_host}/lol/spectator/v5/active-games/by-summoner/{_component(puuid)}"


# --------------------------------------------------------------------------- #
# Parseurs
# --------------------------------------------------------------------------- #


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_account(data: dict[str, Any]) -> AccountDTO:
    return AccountDTO(
        puuid=str(data["puuid"]),
        game_name=str(data.get("gameName") or ""),
        tag_line=str(data.get("tagLine") or ""),
    )


def parse_summoner(data: dict[str, Any]) -> SummonerDTO:
    # `id` (summonerId chiffré) n'est plus renvoyé par les clés récentes → optionnel
    summoner_id = data.get("id")
    return SummonerDTO(
        puuid=str(data["puuid"]),
        summoner_id=str(summoner_id) if summoner_id else None,
        profile_icon_id=_opt_int(data.get("profileIconId")),
        summoner_level=_opt_int(data.get("summonerLevel")),
    )


def parse_league_entries(data: list[dict[str, Any]]) -> list[LeagueEntryDTO]:
    """Ignore les entrées sans tier (ex. files non classiques comme l'Arène)."""
    entries: list[LeagueEntryDTO] = []
    for item in data or []:
        if not isinstance(item, dict) or not item.get("tier"):
            continue
        entries.append(
            LeagueEntryDTO(
                queue_type=str(item.get("queueType") or ""),
                tier=str(item["tier"]).upper(),
                rank=str(item.get("rank") or "I").upper(),
                league_points=int(item.get("leaguePoints") or 0),
                wins=int(item.get("wins") or 0),
                losses=int(item.get("losses") or 0),
                hot_streak=bool(item.get("hotStreak", False)),
            )
        )
    return entries


def parse_match_ids(data: Any) -> list[str]:
    return [str(match_id) for match_id in (data or [])]


def game_start_from_ms(value: Any) -> datetime:
    """`gameStartTime` Spectator (ms) → datetime UTC ; 0 / absent → epoch 0 (début inconnu)."""
    try:
        millis = int(value or 0)
    except (TypeError, ValueError):
        millis = 0
    return datetime.fromtimestamp(max(0, millis) / 1000, tz=timezone.utc)


def parse_active_game(data: dict[str, Any], puuid: str) -> ActiveGameDTO:
    """Spectator-V5 : champion du participant dont le `puuid` correspond (0 si absent)."""
    champions: dict[str, int] = {}
    for participant in data.get("participants") or []:
        if isinstance(participant, dict) and participant.get("puuid"):
            champions[str(participant["puuid"])] = _opt_int(participant.get("championId")) or 0
    champion_id = champions.get(puuid, 0)
    return ActiveGameDTO(
        game_id=int(data.get("gameId") or 0),
        game_start=game_start_from_ms(data.get("gameStartTime")),
        queue_id=int(data.get("gameQueueConfigId") or 0),
        game_mode=str(data.get("gameMode") or ""),
        champion_id=champion_id,
        champion_name=None,
        champions_by_puuid=champions,
    )
