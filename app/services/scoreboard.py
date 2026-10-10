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

from app.db.models import Match, Player, is_remake_game
from app.riot import ddragon

POSITION_ORDER = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
POSITION_LABELS = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid", "BOTTOM": "Bot", "UTILITY": "Support"}
QUEUE_LABELS = {420: "Classée Solo/Duo", 440: "Classée Flex", 400: "Normale", 430: "Normale", 450: "ARAM", 490: "Partie rapide"}


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


def _pings(part: dict[str, Any]) -> int | None:
    """Total des pings (`allInPings`, `dangerPings`, `onMyWayPings`…) ; None si Riot n'en donne aucun."""
    values = [v for k, v in part.items() if k.endswith("Pings") and isinstance(v, int) and not isinstance(v, bool)]
    return sum(values) if values else None


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
            "pings": _pings(part),
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

    remake = is_remake_game(duration, parts)
    # Partie terminée par abandon : Riot le marque pour les 10 joueurs ; l'équipe perdante a abandonné
    surrender = not remake and any(p.get("gameEndedInSurrender") is True for p in parts)
    for team in teams:
        team["surrendered"] = surrender and not team["win"]
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
        "surrender": surrender,
        "gold_diff": team_gold[100] - team_gold[200],
        "teams": teams,
    }


# Chance d'équipe : KDA moyen des 4 coéquipiers rapporté à celui des 5 adversaires (le score MVP,
# fait de la participation aux kills de son propre camp, ne se compare pas d'une équipe à l'autre)
TEAM_LUCK_LEVELS = [(1.3, "Très bonne"), (1.1, "Bonne"), (0.9, "Moyenne"), (0.75, "Mauvaise")]
TEAM_LUCK_WORST = "Très mauvaise"


def team_luck(allies: list[float], enemies: list[float]) -> str | None:
    """« Très bonne » … « Très mauvaise » selon le niveau des coéquipiers face aux adversaires."""
    if not allies or not enemies:
        return None
    ally_mean = sum(allies) / len(allies)
    enemy_mean = sum(enemies) / len(enemies)
    if enemy_mean <= 0:
        return TEAM_LUCK_LEVELS[0][1] if ally_mean > 0 else TEAM_LUCK_LEVELS[2][1]
    ratio = ally_mean / enemy_mean
    return next((label for threshold, label in TEAM_LUCK_LEVELS if ratio >= threshold), TEAM_LUCK_WORST)


def player_highlights(match: Match, player: Player) -> dict[str, Any] | None:
    """Résumé d'un joueur du challenge dans une partie (messages Discord) ; None si indisponible.

    Ligne du tableau des scores (KDA, dégâts, CS, vision, pings, écart d'or, MVP/ACE…) complétée
    par les valeurs par minute, sa place au score parmi les 10 joueurs et la chance d'équipe.
    """
    if not player.puuid or player.id is None:
        return None
    try:
        board = build_scoreboard(match, {player.puuid: player})
    except ScoreboardError:
        return None
    rows = [(team, row) for team in board["teams"] for row in team["players"]]
    mine = next(((team, row) for team, row in rows if row["player_id"] == player.id), None)
    if mine is None:
        return None
    team, row = mine
    minutes = board["duration_s"] / 60 if board["duration_s"] > 0 else 0
    scores = sorted((other["score"] for _, other in rows), reverse=True)
    return {
        **row,
        "duration_s": board["duration_s"],
        "win": team["win"],
        "damage_per_min": round(row["damage"] / minutes) if minutes else None,
        "vision_per_min": round(row["vision_score"] / minutes, 2) if minutes else None,
        "place": scores.index(row["score"]) + 1,
        "players_count": len(rows),
        "team_luck": team_luck(
            [other["kda"] for other in team["players"] if other is not row],
            [other["kda"] for other_team, other in rows if other_team is not team],
        ),
    }


# --------------------------------------------------------------------------- #
# Partie en cours (Spectator-V5) : tableau des 10 joueurs
# --------------------------------------------------------------------------- #


def _side(team_id: int) -> tuple[str, str]:
    if team_id == 100:
        return "blue", "Équipe bleue"
    if team_id == 200:
        return "red", "Équipe rouge"
    return "other", f"Équipe {team_id}" if team_id else "Équipe"


def build_live_board(
    lives: list[Any],
    players_by_puuid: dict[str, Player],
    teams_by_id: dict[int, Any],
    snapshots: dict[int, Any],
    now: datetime,
) -> dict[str, Any]:
    """Tableau d'une partie en cours, à partir des `LiveGameState` du challenge qui y jouent.

    Les 10 joueurs (champion, sorts, runes, Riot ID), les bans, et pour les joueurs du challenge
    leur duo (couleur) et leur rang (dernier snapshot Solo/Duo). Aucun appel Riot : tout vient de
    la réponse Spectator déjà reçue par le poller. `teams` est vide si la composition est inconnue.
    """
    from app.db.session import as_utc  # import local : pas d'autre usage du module
    from app.services.stats import format_rank, rank_color

    first = lives[0]
    board = next((live.board for live in lives if live.board is not None), None)
    starts = [s for s in (as_utc(live.game_start) for live in lives) if s is not None and s.timestamp() > 0]
    loading = not starts
    start = min(starts) if starts else min(as_utc(live.detected_at) or now for live in lives)
    version = ddragon.CURRENT_VERSION
    in_game_ids = {live.player_id for live in lives}

    def challenge_info(player: Player | None) -> dict[str, Any]:
        if player is None:
            return {"is_challenge": False, "player_id": None, "display_name": None, "team_id": None,
                    "team_name": None, "team_color": None, "rank_label": None, "rank_color": None,
                    "rank_crest_url": None}
        team = teams_by_id.get(player.team_id) if player.team_id is not None else None
        snap = snapshots.get(player.id)
        tier = getattr(snap, "tier", None)
        return {
            "is_challenge": True,
            "player_id": player.id,
            "display_name": player.display_name,
            "team_id": getattr(team, "id", None),
            "team_name": getattr(team, "name", None),
            "team_color": getattr(team, "color", None),
            "rank_label": format_rank(tier, snap.rank, snap.lp) if snap is not None else None,
            "rank_color": rank_color(tier) if snap is not None else None,
            "rank_crest_url": ddragon.rank_mini_crest_url(tier),
        }

    teams: list[dict[str, Any]] = []
    challenge_players: list[dict[str, Any]] = []
    if board is not None:
        by_team: dict[int, list[Any]] = {}
        for participant in board.participants:
            by_team.setdefault(participant.team_id, []).append(participant)
        for team_id in sorted(by_team, key=lambda t: (t not in (100, 200), t)):
            side, side_label = _side(team_id)
            members = by_team[team_id]
            if all(m.position in POSITION_ORDER for m in members):
                members = sorted(members, key=lambda m: POSITION_ORDER.index(m.position))
            rows = []
            for participant in members:
                image = participant.champion_name or ddragon.cached_champion_name(participant.champion_id)
                keystone_name, keystone_url = ddragon.rune_icon(participant.keystone_id)
                style_name, style_url = ddragon.rune_icon(participant.sub_style_id, style=True)
                player = players_by_puuid.get(participant.puuid) if participant.puuid else None
                row = {
                    "riot_name": participant.riot_name,
                    "riot_tag": participant.riot_tag,
                    "bot": participant.bot,
                    "profile_icon_url": ddragon.profile_icon_url(version, participant.profile_icon_id),
                    "champion_id": participant.champion_id,
                    "champion_name": ddragon.champion_display_name(image) if image else f"Champion {participant.champion_id}",
                    "champion_icon_url": ddragon.champion_icon_url(version, image) if image else None,
                    "spell_urls": [ddragon.spell_icon_url(version, s) for s in participant.spell_ids],
                    "keystone": keystone_name,
                    "keystone_url": keystone_url,
                    "secondary_style": style_name,
                    "secondary_style_url": style_url,
                    "position": participant.position,
                    "position_label": POSITION_LABELS.get(participant.position or "", ""),
                    "position_icon_url": ddragon.position_icon_url(participant.position),
                    **challenge_info(player),
                }
                rows.append(row)
                if row["is_challenge"]:
                    challenge_players.append(
                        {key: row[key] for key in ("player_id", "display_name", "team_id", "team_name", "team_color")}
                        | {"side": side, "champion_name": row["champion_name"]}
                    )
            bans = [
                {
                    "champion_id": ban.champion_id,
                    "champion_name": ddragon.champion_display_name(ban.champion_name or ddragon.cached_champion_name(ban.champion_id))
                    or f"Champion {ban.champion_id}",
                    "champion_icon_url": ddragon.champion_icon_url(
                        version, ban.champion_name or ddragon.cached_champion_name(ban.champion_id)
                    ),
                    "pick_turn": ban.pick_turn,
                }
                for ban in sorted(board.bans, key=lambda b: b.pick_turn)
                if ban.team_id == team_id
            ]
            teams.append(
                {
                    "team_id": team_id,
                    "side": side,
                    "side_label": side_label,
                    "has_challenge_player": any(r["is_challenge"] for r in rows),
                    "bans": bans,
                    "players": rows,
                }
            )
    # Joueurs du challenge en partie que la composition ne montre pas (Spectator incomplet)
    shown = {c["player_id"] for c in challenge_players}
    for live in lives:
        if live.player_id in shown:
            continue
        player = next((p for p in players_by_puuid.values() if p.id == live.player_id), None)
        if player is None:
            continue
        info = challenge_info(player)
        challenge_players.append(
            {key: info[key] for key in ("player_id", "display_name", "team_id", "team_name", "team_color")}
            | {"side": None, "champion_name": ddragon.champion_display_name(live.champion_name)}
        )
    # Duo ensemble = deux joueurs du même duo dans la même équipe (en face, c'est un duel)
    duo_counts: dict[Any, int] = {}
    for cp in challenge_players:
        key = (cp["team_id"], cp["side"])
        duo_counts[key] = duo_counts.get(key, 0) + 1
    return {
        "game_id": first.game_id,
        "queue_id": first.queue_id,
        "queue_label": QUEUE_LABELS.get(int(first.queue_id or 0), "Partie"),
        "game_mode": first.game_mode,
        "map_id": getattr(board, "map_id", None),
        "game_start": start.isoformat(),
        "loading": loading,
        "elapsed_s": max(0, int((now - start).total_seconds())),
        "challenge_players": challenge_players,
        "in_game_player_ids": sorted(in_game_ids),
        "duo_together": any(n >= 2 for (duo_id, _side), n in duo_counts.items() if duo_id is not None),
        "versus": len({cp["side"] for cp in challenge_players if cp["side"]}) > 1,
        "teams": teams,
    }
