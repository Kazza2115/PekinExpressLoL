"""Tableau des scores d'une partie (style op.gg) depuis le JSON Match-V5 stocké."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db.models import Match, Player
from app.services.scoreboard import ScoreboardError, build_scoreboard
from tests.conftest import ADMIN_PASSWORD

H = {"X-Admin-Password": ADMIN_PASSWORD}
POSITIONS = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]


def participant(i: int, team: int, *, kills=3, deaths=3, assists=5, gold=10000, damage=15000, puuid=None, **extra):
    return {
        "puuid": puuid or f"p{i}",
        "riotIdGameName": f"Joueur{i}",
        "riotIdTagline": "EUW",
        "championName": "Ahri",
        "championId": 103,
        "champLevel": 15,
        "teamId": team,
        "teamPosition": POSITIONS[i % 5],
        "win": team == 100,
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "goldEarned": gold,
        "totalDamageDealtToChampions": damage,
        "totalDamageTaken": 20000,
        "totalMinionsKilled": 180,
        "neutralMinionsKilled": 12,
        "visionScore": 20,
        "wardsPlaced": 9,
        "wardsKilled": 3,
        "visionWardsBoughtInGame": 2,
        "summoner1Id": 4,
        "summoner2Id": 14,
        "item0": 3089,
        "item6": 3340,
        "perks": {"styles": [
            {"description": "primaryStyle", "style": 8200, "selections": [{"perk": 8229}]},
            {"description": "subStyle", "style": 8300, "selections": []},
        ]},
        **extra,
    }


def match_of(parts, duration=1800) -> Match:
    raw = {
        "metadata": {"matchId": "EUW1_42"},
        "info": {
            "gameStartTimestamp": 1_791_600_000_000,
            "gameEndTimestamp": 1_791_600_000_000 + duration * 1000,
            "gameDuration": duration,
            "gameVersion": "14.19.620.1234",
            "queueId": 420,
            "participants": parts,
            "teams": [
                {"teamId": 100, "win": True, "bans": [], "objectives": {"tower": {"kills": 9}, "dragon": {"kills": 3}, "baron": {"kills": 1}}},
                {"teamId": 200, "win": False, "bans": [], "objectives": {"tower": {"kills": 2}, "dragon": {"kills": 1}}},
            ],
        },
    }
    return Match(match_id="EUW1_42", queue_id=420, game_start=datetime.now(timezone.utc), game_duration=duration, raw_json=json.dumps(raw))


def test_scoreboard_teams_players_and_gold() -> None:
    parts = [participant(i, 100, gold=12000) for i in range(5)] + [participant(i + 5, 200, gold=10000) for i in range(5)]
    parts[0].update(puuid="p-mike", kills=12, deaths=1, assists=8, damage=40000, largestMultiKill=3, firstBloodKill=True)
    board = build_scoreboard(match_of(parts), {"p-mike": Player(id=7, display_name="Mike")})
    assert board["queue_label"] == "Classée Solo/Duo" and board["patch"] == "14.19" and board["duration_s"] == 1800
    blue, red = board["teams"]
    assert (blue["side"], blue["win"], red["win"]) == ("blue", True, False)
    assert [p["position"] for p in blue["players"]] == POSITIONS
    assert blue["objectives"]["tower"] == 9 and red["objectives"]["dragon"] == 1
    assert board["gold_diff"] == 5 * 12000 - 5 * 10000
    mike = blue["players"][0]
    assert mike["player_id"] == 7 and mike["display_name"] == "Mike"
    assert mike["badge"] == "MVP" and mike["multi_kill_label"] == "Triple" and mike["first_blood"] is True
    assert mike["gold_diff_lane"] == 2000  # face au top adverse
    assert mike["damage_pct_of_max"] == 100.0 and mike["kill_participation"] == round((12 + 8) / (12 + 4 * 3) * 100, 1)
    assert mike["keystone"] == "Comète arcanique" and mike["keystone_url"].endswith("ArcaneComet.png")
    assert mike["secondary_style_url"].endswith("7203_Whimsy.png")
    assert mike["cs"] == 192 and mike["cs_per_min"] == 6.4
    assert sum(1 for p in red["players"] if p["badge"] == "ACE") == 1


def test_scoreboard_tolerates_missing_fields_and_remakes() -> None:
    parts = [{"teamId": 100, "win": False}, {"teamId": 200, "win": True, "championName": "Garen"}]
    board = build_scoreboard(match_of(parts, duration=200), {})
    assert board["remake"] is True
    assert all(p["badge"] is None for t in board["teams"] for p in t["players"])
    assert board["teams"][0]["players"][0]["keystone_url"] is None
    with pytest.raises(ScoreboardError):
        build_scoreboard(Match(match_id="X", queue_id=420, game_start=datetime.now(timezone.utc), game_duration=1, raw_json="{}"), {})


def test_match_endpoint_with_a_demo_game(client: TestClient) -> None:
    for name, riot in (("Mike", "Mike Demo#EUW"), ("Léa", "Lea Demo#EUW")):
        assert client.post("/api/players", json={"display_name": name, "riot_id": riot}).status_code == 201
    client.post("/api/admin/teams", headers=H, json={"player_ids": [1, 2]})
    assert client.post("/api/admin/challenge/start", headers=H, json={}).status_code == 200
    for _ in range(3):
        client.post("/api/admin/refresh", headers=H)
    items = client.get("/api/feed").json()["items"]
    assert items, "le client démo doit avoir enregistré au moins une partie"
    board = client.get(f"/api/matches/{items[0]['match_id']}").json()["match"]
    assert len(board["teams"]) == 2 and all(len(t["players"]) == 5 for t in board["teams"])
    ours = [p for t in board["teams"] for p in t["players"] if p["player_id"]]
    assert ours and all(p["display_name"] in ("Mike", "Léa") for p in ours)
    assert board["gold_diff"] == board["teams"][0]["gold"] - board["teams"][1]["gold"]
    assert all(p["keystone_url"] for t in board["teams"] for p in t["players"])  # runes de démo
    assert client.get("/api/matches/EUW1_INCONNU").status_code == 404
