"""Sérialisation JSON des modèles (source unique de vérité pour l'API et les pages).

Toutes les dates sont renvoyées en ISO 8601 UTC via `as_utc()` (SQLite rend des
datetimes naïfs). Fonctions pures : aucun accès à la base ici.
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any
from urllib.parse import quote

from app.db.models import Challenge, Match, MatchParticipant, Player, RankSnapshot, Team, game_end_of
from app.db.session import as_utc
from app.riot import ddragon
from app.services.stats import format_rank, kda, rank_color

OPGG_BASE = "https://www.op.gg/summoners/euw"
# Emplacements d'objets Match-V5 : 6 objets + bibelot
ITEM_SLOT_COUNT = 7


def parse_items(raw: str | None) -> list[int]:
    """Colonne `MatchParticipant.items` (JSON) → 7 ids (0 = vide) ; 7 zéros si absente/illisible."""
    empty = [0] * ITEM_SLOT_COUNT
    if not raw:
        return empty
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        return empty
    if not isinstance(values, list):
        return empty
    items: list[int] = []
    for value in values[:ITEM_SLOT_COUNT]:
        try:
            items.append(max(0, int(value)))
        except (TypeError, ValueError):
            items.append(0)
    return items + [0] * (ITEM_SLOT_COUNT - len(items))


def parse_spells(raw: str | None) -> list[int]:
    """Colonne `MatchParticipant.spells` ("4,14") → [4, 14] ; [] si absente/illisible."""
    if not raw:
        return []
    spells: list[int] = []
    for part in raw.split(","):
        try:
            spells.append(int(part.strip()))
        except ValueError:
            continue
    return spells


def iso(dt: datetime | None) -> str | None:
    """Datetime (éventuellement naïf) → chaîne ISO UTC, ou None."""
    value = as_utc(dt)
    return value.isoformat() if value is not None else None


def enum_value(value: Any) -> Any:
    """Enum → valeur brute (les colonnes SQLModel peuvent renvoyer l'enum ou la chaîne)."""
    return value.value if isinstance(value, Enum) else value


def challenge_to_dict(challenge: Challenge) -> dict[str, Any]:
    return {
        "id": challenge.id,
        "name": challenge.name,
        "status": enum_value(challenge.status),
        "start_at": iso(challenge.start_at),
        "end_at": iso(challenge.end_at),
        "games_per_day": challenge.games_per_day,
        "track_flex": challenge.track_flex,
    }


def opgg_url(player: Player | None) -> str | None:
    """Lien op.gg du compte (None si le compte n'est pas lié)."""
    if player is None or not player.game_name or not player.tag_line:
        return None
    return f"{OPGG_BASE}/{quote(player.game_name, safe='')}-{quote(player.tag_line, safe='')}"


def player_public(player: Player, last_solo_snapshot: RankSnapshot | None = None) -> dict[str, Any]:
    """`PlayerPublic` : identité + rang courant (dernier snapshot SOLO)."""
    snapshot = last_solo_snapshot
    tier = snapshot.tier if snapshot is not None else None
    rank = snapshot.rank if snapshot is not None else None
    lp = snapshot.lp if snapshot is not None else 0
    return {
        "id": player.id,
        "display_name": player.display_name,
        "riot_id": player.riot_id,
        "game_name": player.game_name,
        "tag_line": player.tag_line,
        "is_linked": player.is_linked,
        "link_error": player.link_error,
        "active": player.active,
        "team_id": player.team_id,
        "profile_icon_id": player.profile_icon_id,
        "icon_url": ddragon.profile_icon_url(ddragon.CURRENT_VERSION, player.profile_icon_id),
        "summoner_level": player.summoner_level,
        "tier": tier,
        "rank": rank,
        "lp": lp,
        "rank_label": format_rank(tier, rank, lp),
        "rank_color": rank_color(tier),
        "rank_emblem_url": ddragon.rank_emblem_url(tier),
        "rank_crest_url": ddragon.rank_mini_crest_url(tier),
        "created_at": iso(player.created_at),
        "linked_at": iso(player.linked_at),
    }


def team_public(team: Team, player_ids: list[int]) -> dict[str, Any]:
    """`TeamPublic`."""
    return {
        "id": team.id,
        "name": team.name,
        "color": team.color,
        "slot": team.slot,
        "window_start": iso(team.window_start),
        "window_end": iso(team.window_end),
        "player_ids": list(player_ids),
    }


def match_row(
    participant: MatchParticipant,
    player: Player | None,
    match: Match | None = None,
) -> dict[str, Any]:
    """`MatchRow` : la ligne d'un joueur dans une partie (feed, fiche joueur)."""
    duration = participant.game_duration or (match.game_duration if match is not None else 0) or 0
    cs_per_min = round(participant.cs / (duration / 60), 1) if duration > 0 else 0.0
    version = ddragon.CURRENT_VERSION
    items = parse_items(participant.items)
    spells = parse_spells(participant.spells)
    return {
        "match_id": participant.match_id,
        "player_id": participant.player_id,
        "queue": enum_value(participant.queue),
        "game_start": iso(participant.game_start),
        "game_end": game_end_of(participant).isoformat(),
        "game_duration": duration,
        "champion_name": participant.champion_name,
        "champion_icon_url": ddragon.champion_icon_url(version, participant.champion_name),
        "champion_splash_url": ddragon.champion_splash_url(participant.champion_name),
        "champion_loading_url": ddragon.champion_loading_url(participant.champion_name),
        "position": participant.position,
        "position_icon_url": ddragon.position_icon_url(participant.position),
        "team_side": participant.team_side,
        "champ_level": participant.champ_level,
        "kill_participation": participant.kill_participation,
        "items": items,
        "item_urls": [ddragon.item_icon_url(version, item) for item in items],
        "spells": spells,
        "spell_urls": [ddragon.spell_icon_url(version, spell) for spell in spells],
        "win": participant.win,
        "kills": participant.kills,
        "deaths": participant.deaths,
        "assists": participant.assists,
        "kda": round(kda(participant.kills, participant.deaths, participant.assists), 2),
        "cs": participant.cs,
        "cs_per_min": cs_per_min,
        "gold": participant.gold,
        "damage_to_champions": participant.damage_to_champions,
        "vision_score": participant.vision_score,
        "lp_change": participant.lp_change,
        "is_remake": participant.is_remake,
        "opgg_url": opgg_url(player),
    }


def snapshot_row(snapshot: RankSnapshot) -> dict[str, Any]:
    """`SnapshotRow` : photo du rang (fiche joueur)."""
    return {
        "id": snapshot.id,
        "player_id": snapshot.player_id,
        "queue": enum_value(snapshot.queue),
        "tier": snapshot.tier,
        "rank": snapshot.rank,
        "lp": snapshot.lp,
        "wins": snapshot.wins,
        "losses": snapshot.losses,
        "hot_streak": snapshot.hot_streak,
        "absolute_lp": snapshot.absolute_lp,
        "rank_label": format_rank(snapshot.tier, snapshot.rank, snapshot.lp),
        "rank_color": rank_color(snapshot.tier),
        "captured_at": iso(snapshot.captured_at),
    }


def lp_point(snapshot: RankSnapshot) -> dict[str, Any]:
    """Point d'une courbe de LP : `{t, absolute_lp, tier, rank, lp}`."""
    return {
        "t": iso(snapshot.captured_at),
        "absolute_lp": snapshot.absolute_lp,
        "tier": snapshot.tier,
        "rank": snapshot.rank,
        "lp": snapshot.lp,
    }
