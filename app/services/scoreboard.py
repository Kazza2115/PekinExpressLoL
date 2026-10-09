"""Tableau des scores d'une partie (style op.gg) à partir du JSON Match-V5 stocké.

Les 10 joueurs (alliés et ennemis) : champion, sorts, runes, objets, KDA, participation aux
kills, dégâts infligés et subis, or (et écart avec l'adversaire de la même voie), CS, vision ;
les totaux et objectifs de chaque équipe, et l'écart d'or entre les équipes. Les joueurs du
challenge sont reconnus (lien vers leur fiche). MVP (meilleur score du camp vainqueur) et ACE
(meilleur score du camp perdant), selon un score simple : KDA + participation + part des dégâts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.db.models import Match, Player
from app.riot import ddragon

POSITION_ORDER = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
POSITION_LABELS = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Support"}
QUEUE_LABELS = {420: "Classée Solo/Duo", 440: "Classée Flex", 400: "Normale", 430: "Normale", 450: "ARAM", 490: "Partie rapide"}
REMAKE_MAX_DURATION_S = 300


class ScoreboardError(ValueError):
    """JSON de partie absent ou illisible."""


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _duration_s(info: dict[str, Any]) -> int:
    duration = _int(info.get("gameDuration"))
    # Règle Riot : millisecondes si `gameEndTimestamp` est absent (anciennes parties)
    if "gameEndTimestamp" not in info or duration > 100_000:
        duration //= 1000
    return duration


def _iso_ms(value: Any) -> str | None:
    ms = _int(value)
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if ms > 0 else None


def _perks(part: dict[str, Any]) -> tuple[int | None, int | None]:
    """(rune principale, arbre secondaire) depuis `perks.styles`."""
    styles = (part.get("perks") or {}).get("styles") or []
    keystone = secondary = None
    for style in styles:
        if not isinstance(style, dict):
            continue
        if style.get("description") == "primaryStyle":
            selections = style.get("selections") or []
            if selections and isinstance(selections[0], dict):
                keystone = _int(selections[0].get("perk")) or None
        elif style.get("description") == "subStyle":
            secondary = _int(style.get("style")) or None
    return keystone, secondary


def _riot_name(part: dict[str, Any]) -> tuple[str, str | None]:
    name = part.get("riotIdGameName") or part.get("summonerName") or "Joueur"
    tag = part.get("riotIdTagline") or None
    return str(name), (str(tag) if tag else None)


def _score(kda: float, kp: float | None, damage_share: float | None) -> float:
    return round(kda + (kp or 0) / 20 + (damage_share or 0) / 10, 2)


def build_scoreboard(match: Match, players_by_puuid: dict[str, Player]) -> dict[str, Any]:
    """Tableau des scores complet d'une partie ; lève `ScoreboardError` si le JSON manque."""
    try:
        raw = json.loads(match.raw_json or "")
    except ValueError as exc:
        raise ScoreboardError("Détail de la partie indisponible.") from exc
    info = raw.get("info") if isinstance(raw, dict) else None
    if not isinstance(info, dict):
        raise ScoreboardError("Détail de la partie indisponible.")
    parts = [p for p in info.get("participants") or [] if isinstance(p, dict)]
    if not parts:
        raise ScoreboardError("Détail de la partie indisponible.")

    version = ddragon.CURRENT_VERSION
    duration = _duration_s(info)
    minutes = duration / 60 if duration > 0 else 0
    sides = {100: [p for p in parts if _int(p.get("teamId")) == 100], 200: [p for p in parts if _int(p.get("teamId")) == 200]}
    team_kills = {side: sum(_int(p.get("kills")) for p in members) for side, members in sides.items()}
    team_gold = {side: sum(_int(p.get("goldEarned")) for p in members) for side, members in sides.items()}
    team_damage = {side: sum(_int(p.get("totalDamageDealtToChampions")) for p in members) for side, members in sides.items()}
    max_damage = max((_int(p.get("totalDamageDealtToChampions")) for p in parts), default=0) or 1
    max_taken = max((_int(p.get("totalDamageTaken")) for p in parts), default=0) or 1
    by_lane = {(_int(p.get("teamId")), p.get("teamPosition") or ""): p for p in parts}

    def row(part: dict[str, Any]) -> dict[str, Any]:
        side = _int(part.get("teamId"))
        kills, deaths, assists = _int(part.get("kills")), _int(part.get("deaths")), _int(part.get("assists"))
        kda = (kills + assists) / deaths if deaths else float(kills + assists)
        kp = round(min(100.0, (kills + assists) / team_kills[side] * 100), 1) if team_kills.get(side) else None
        damage = _int(part.get("totalDamageDealtToChampions"))
        share = round(damage / team_damage[side] * 100, 1) if team_damage.get(side) else None
        cs = _int(part.get("totalMinionsKilled")) + _int(part.get("neutralMinionsKilled"))
        gold = _int(part.get("goldEarned"))
        position = part.get("teamPosition") or None
        opponent = by_lane.get((300 - side, position or "")) if position else None
        name, tag = _riot_name(part)
        player = players_by_puuid.get(part.get("puuid") or "")
        keystone, secondary = _perks(part)
        keystone_name, keystone_url = ddragon.rune_icon(keystone)
        style_name, style_url = ddragon.rune_icon(secondary, style=True)
        items = [_int(part.get(f"item{i}")) for i in range(7)]
        spells = [_int(part.get("summoner1Id")), _int(part.get("summoner2Id"))]
        multi = _int(part.get("largestMultiKill"))
        return {
            "puuid_known": player is not None,
            "player_id": player.id if player is not None else None,
            "display_name": player.display_name if player is not None else None,
            "riot_name": name,
            "riot_tag": tag,
            "champion_name": part.get("championName") or "",
            "champion_icon_url": ddragon.champion_icon_url(version, part.get("championName")),
            "champ_level": _int(part.get("champLevel")) or None,
            "position": position,
            "position_label": POSITION_LABELS.get(position or "", ""),
            "position_icon_url": ddragon.position_icon_url(position),
            "spell_urls": [ddragon.spell_icon_url(version, s) for s in spells],
            "keystone": keystone_name,
            "keystone_url": keystone_url,
            "secondary_style": style_name,
            "secondary_style_url": style_url,
            "items": items,
            "item_urls": [ddragon.item_icon_url(version, item) for item in items],
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "kda": round(kda, 2),
            "perfect_kda": deaths == 0,
            "kill_participation": kp,
            "damage": damage,
            "damage_share": share,
            "damage_pct_of_max": round(damage / max_damage * 100, 1),
            "damage_taken": _int(part.get("totalDamageTaken")),
            "damage_taken_pct_of_max": round(_int(part.get("totalDamageTaken")) / max_taken * 100, 1),
            "gold": gold,
            "gold_diff_lane": (gold - _int(opponent.get("goldEarned"))) if opponent is not None else None,
            "cs": cs,
            "cs_per_min": round(cs / minutes, 1) if minutes else None,
            "vision_score": _int(part.get("visionScore")),
            "wards_placed": _int(part.get("wardsPlaced")),
            "wards_killed": _int(part.get("wardsKilled")),
            "control_wards": _int(part.get("visionWardsBoughtInGame")),
            "largest_multi_kill": multi,
            "multi_kill_label": {2: "Double", 3: "Triple", 4: "Quadra", 5: "Penta"}.get(min(multi, 5)) if multi >= 2 else None,
            "first_blood": bool(part.get("firstBloodKill")),
            "score": _score(kda, kp, share),
            "badge": None,
        }

    raw_teams = {_int(t.get("teamId")): t for t in info.get("teams") or [] if isinstance(t, dict)}
    teams: list[dict[str, Any]] = []
    for side in (100, 200):
        members = sorted(
            sides[side],
            key=lambda p: POSITION_ORDER.index(p.get("teamPosition")) if p.get("teamPosition") in POSITION_ORDER else 9,
        )
        rows = [row(p) for p in members]
        objectives = (raw_teams.get(side) or {}).get("objectives") or {}
        win = bool((raw_teams.get(side) or {}).get("win")) if raw_teams.get(side) else any(p.get("win") for p in members)
        teams.append(
            {
                "side": "blue" if side == 100 else "red",
                "side_label": "Équipe bleue" if side == 100 else "Équipe rouge",
                "win": win,
                "kills": team_kills[side],
                "deaths": sum(r["deaths"] for r in rows),
                "assists": sum(r["assists"] for r in rows),
                "gold": team_gold[side],
                "damage": team_damage[side],
                "objectives": {
                    key: _int((objectives.get(key) or {}).get("kills"))
                    for key in ("tower", "inhibitor", "dragon", "baron", "riftHerald", "horde")
                },
                "bans": [
                    {
                        "champion_id": _int(ban.get("championId")),
                        "champion_name": ddragon.cached_champion_name(_int(ban.get("championId"))),
                        "champion_icon_url": ddragon.champion_icon_url(
                            version, ddragon.cached_champion_name(_int(ban.get("championId")))
                        ),
                        "pick_turn": _int(ban.get("pickTurn")),
                    }
                    for ban in (raw_teams.get(side) or {}).get("bans") or []
                    if isinstance(ban, dict) and _int(ban.get("championId")) > 0
                ],
                "players": rows,
            }
        )

    remake = duration < REMAKE_MAX_DURATION_S
    if not remake:
        for team in teams:
            if team["players"]:
                best = max(team["players"], key=lambda r: r["score"])
                best["badge"] = "MVP" if team["win"] else "ACE"

    queue_id = _int(info.get("queueId"))
    patch = ".".join(str(info.get("gameVersion") or "").split(".")[:2]) or None
    return {
        "match_id": match.match_id,
        "queue_id": queue_id,
        "queue_label": QUEUE_LABELS.get(queue_id, "Partie"),
        "game_start": _iso_ms(info.get("gameStartTimestamp") or info.get("gameCreation")),
        "game_end": _iso_ms(info.get("gameEndTimestamp")),
        "duration_s": duration,
        "patch": patch,
        "remake": remake,
        "gold_diff": team_gold[100] - team_gold[200],
        "teams": teams,
    }
