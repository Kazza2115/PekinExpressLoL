"""Constructeurs d'URL et parseurs typés de l'API Riot (utilisés par `RiotClient`).

Aucune logique réseau ici : uniquement la correspondance entre les JSON Riot et
les DTO de `app.riot.base`. Les composants de chemin sont encodés avec
`urllib.parse.quote` (un Riot ID peut contenir des espaces ou des accents).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode

from app.riot.base import (
    AccountDTO,
    ActiveGameDTO,
    ActiveParticipantDTO,
    BannedChampionDTO,
    LeagueEntryDTO,
    SummonerDTO,
)


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


def _parse_active_participant(raw: dict[str, Any]) -> ActiveParticipantDTO:
    """Participant Spectator-V5 → DTO (Riot ID « Nom#TAG », sorts, runes, icône)."""
    riot_id = str(raw.get("riotId") or "").strip()
    name, sep, tag = riot_id.rpartition("#")
    if not sep:
        name, tag = riot_id, ""
    # Vide si inconnu (bot, Riot ID masqué) : le site affiche alors « Bot » ou « Joueur masqué »
    name = name.strip() or str(raw.get("summonerName") or "").strip()
    perks = raw.get("perks") if isinstance(raw.get("perks"), dict) else {}
    perk_ids = [p for p in (perks.get("perkIds") or []) if isinstance(p, int) and not isinstance(p, bool) and p > 0]
    return ActiveParticipantDTO(
        puuid=str(raw["puuid"]) if raw.get("puuid") else None,
        riot_name=name,
        riot_tag=tag.strip() or None,
        team_id=_opt_int(raw.get("teamId")) or 0,
        champion_id=_opt_int(raw.get("championId")) or 0,
        spell_ids=(_opt_int(raw.get("spell1Id")) or 0, _opt_int(raw.get("spell2Id")) or 0),
        keystone_id=perk_ids[0] if perk_ids else None,
        primary_style_id=_opt_int(perks.get("perkStyle")),
        sub_style_id=_opt_int(perks.get("perkSubStyle")),
        profile_icon_id=_opt_int(raw.get("profileIconId")),
        bot=bool(raw.get("bot")),
    )


def parse_active_game(data: dict[str, Any], puuid: str) -> ActiveGameDTO:
    """Spectator-V5 : la partie du joueur `puuid`, avec la composition complète (10 joueurs, bans)."""
    participants = [
        _parse_active_participant(raw) for raw in data.get("participants") or [] if isinstance(raw, dict)
    ]
    champions = {p.puuid: p.champion_id for p in participants if p.puuid}
    bans = [
        BannedChampionDTO(
            team_id=_opt_int(ban.get("teamId")) or 0,
            champion_id=_opt_int(ban.get("championId")) or 0,
            pick_turn=_opt_int(ban.get("pickTurn")) or 0,
        )
        for ban in data.get("bannedChampions") or []
        if isinstance(ban, dict) and (_opt_int(ban.get("championId")) or 0) > 0  # -1 : pas de ban
    ]
    return ActiveGameDTO(
        game_id=int(data.get("gameId") or 0),
        game_start=game_start_from_ms(data.get("gameStartTime")),
        queue_id=int(data.get("gameQueueConfigId") or 0),
        game_mode=str(data.get("gameMode") or ""),
        champion_id=champions.get(puuid, 0),
        champion_name=None,
        champions_by_puuid=champions,
        participants=participants,
        bans=bans,
        map_id=_opt_int(data.get("mapId")),
    )
