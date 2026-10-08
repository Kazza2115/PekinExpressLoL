"""Endpoints du profil joueur : `/api/duos` (comparatif, échelle), `/api/rankings`, `/api/players/{id}`."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import event

from app.services.stats import RANKING_METRICS, RECORD_KEYS
from tests.test_api import LEA, MIKE, SAM, _admin_post, _create_team, _register
from tests.test_profile_stats import EXPECTED_METRICS, LADDER_KEYS

MATCH_DETAIL_KEYS = {
    "double_kills", "triple_kills", "quadra_kills", "penta_kills", "largest_multi_kill", "first_blood_kill",
    "damage_taken", "damage_share", "gold_share", "wards_placed", "wards_killed", "control_wards_bought",
    "time_ccing_others", "time_spent_dead", "turret_kills", "dragon_kills", "baron_kills", "objectives_stolen",
    "surrendered", "gold_per_min", "damage_per_min",
}
PLAYER_PROFILE_KEYS = {
    "summoner_level", "kills", "deaths", "assists", "avg_kills", "avg_deaths", "avg_assists",
    "avg_kill_participation", "avg_cs", "avg_gold", "avg_gold_per_min", "avg_damage_per_min", "avg_damage_share",
    "avg_damage_taken", "avg_heal", "avg_cc_time", "avg_time_dead", "avg_wards_placed", "avg_wards_killed",
    "avg_control_wards", "double_kills", "triple_kills", "quadra_kills", "penta_kills", "multikills",
    "first_bloods", "largest_killing_spree", "largest_multi_kill", "turret_kills", "dragon_kills", "baron_kills",
    "objectives_stolen", "surrenders", "avg_game_duration", "total_time_played", "longest_game_s",
    "shortest_game_s", "lp_per_game", "lp_known_games", "avg_lp_win", "avg_lp_loss", "best_lp_gain",
    "worst_lp_loss", "games_blue", "wins_blue", "winrate_blue", "games_red", "wins_red", "winrate_red",
    "by_position", "by_duration", "by_day", "by_hour", "records", "season_wins", "season_losses",
    "season_winrate", "peak_absolute_lp", "peak_rank_label", "low_absolute_lp", "low_rank_label", "promotions",
    "demotions", "rank_delta_lp", "partner",
}
TEAM_PROFILE_KEYS = {
    "avg_absolute_lp", "rank_label", "rank_color", "top_player_id", "top_player_rank_label", "kills", "deaths",
    "assists", "avg_kill_participation", "lp_per_game", "best_win_streak", "multikills", "penta_kills",
    "first_bloods", "dragon_kills", "baron_kills", "turret_kills", "objectives_stolen", "surrenders",
    "avg_game_duration", "total_time_played", "avg_damage_share", "avg_gold_per_min", "avg_wards_placed",
    "season_wins", "season_losses", "season_winrate",
}
PARTNER_KEYS = {
    "player_id", "display_name", "icon_url", "together_games", "together_wins", "together_losses",
    "together_winrate", "solo_games", "solo_wins", "solo_losses", "solo_winrate",
}


def _running_challenge_with_games(client: TestClient, headers: dict) -> None:
    """8 joueurs démo → tirage → démarrage → quelques cycles (le client démo joue à chaque tick)."""
    assert len(client.post("/api/demo/fill").json()["players"]) == 8
    _admin_post(client, headers, "/api/admin/draw")
    _admin_post(client, headers, "/api/admin/challenge/start")
    for _ in range(6):
        _admin_post(client, headers, "/api/admin/refresh")


def test_profile_endpoints_on_demo_data(client: TestClient, admin_headers: dict, engine) -> None:  # noqa: ANN001
    _running_challenge_with_games(client, admin_headers)

    # Les routes de lecture ne relisent jamais le JSON Match-V5 brut
    statements: list[str] = []

    def capture(_conn, _cursor, statement, *_args):  # noqa: ANN001
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        duos = client.get("/api/duos").json()
        rankings = client.get("/api/rankings").json()
        player_id = duos["teams"][0]["players"][0]["player_id"]
        detail = client.get(f"/api/players/{player_id}").json()
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert statements and not any("raw_json" in statement for statement in statements)

    # --- /api/duos : comparatif + échelle des rangs ---------------------------------
    teams = duos["teams"]
    assert len(teams) == 4
    for team in teams:
        assert TEAM_PROFILE_KEYS <= set(team)
        assert team["rank_label"] and team["top_player_id"] in {p["player_id"] for p in team["players"]}
        for player in team["players"]:
            assert PLAYER_PROFILE_KEYS <= set(player)
            assert set(player["partner"]) == PARTNER_KEYS
    metrics = duos["comparison"]["metrics"]
    assert [m["key"] for m in metrics] == EXPECTED_METRICS
    team_ids = [t["team_id"] for t in teams]
    for metric in metrics:
        assert [v["team_id"] for v in metric["values"]] == team_ids
        assert metric["best_team_id"] in (None, *team_ids) and metric["worst_team_id"] in (None, *team_ids)
    games_metric = next(m for m in metrics if m["key"] == "games")
    assert [v["value"] for v in games_metric["values"]] == [t["games"] for t in teams]
    assert len(duos["ladder"]) == 8 and all(set(entry) == LADDER_KEYS for entry in duos["ladder"])
    assert duos["ladder"] == rankings["players"]

    # --- /api/rankings ---------------------------------------------------------------
    assert set(rankings) == {"challenge", "generated_at", "players", "teams", "tiers", "summary"}
    assert rankings["challenge"]["status"] == "running" and rankings["generated_at"]
    positions = [p["position"] for p in rankings["players"]]
    assert positions == list(range(1, 9))  # comptes démo tous classés
    values = [p["absolute_lp"] for p in rankings["players"]]
    assert values == sorted(values, reverse=True)
    assert all(p["team_name"] and p["rank_emblem_url"] for p in rankings["players"])
    assert len(rankings["teams"]) == 4 and [t["position"] for t in rankings["teams"]] == [1, 2, 3, 4]
    assert sum(t["count"] for t in rankings["tiers"]) == 8
    assert rankings["summary"]["ranked_players"] == 8 and rankings["summary"]["unranked_players"] == 0
    assert rankings["summary"]["highest"]["player_id"] == rankings["players"][0]["player_id"]

    # --- /api/players/{id} -----------------------------------------------------------
    assert set(detail) == {"player", "team", "stats", "rankings", "team_stats", "matches", "snapshots"}
    stats = detail["stats"]
    assert PLAYER_PROFILE_KEYS <= set(stats) and stats["games"] > 0
    assert len(stats["by_duration"]) == 3 and len(stats["by_hour"]) == 4 and stats["by_day"]
    assert set(stats["records"]) == set(RECORD_KEYS) and stats["records"]["longest_game"] is not None
    assert stats["kills"] is not None and stats["avg_wards_placed"] is not None
    mate = next(p for p in duos["teams"][0]["players"] if p["player_id"] != player_id)
    assert stats["partner"]["player_id"] == mate["player_id"]
    assert set(detail["rankings"]) == {key for key, _ in RANKING_METRICS}
    for entry in detail["rankings"].values():
        assert set(entry) == {"position", "total", "value", "higher_is_better"}
        assert entry["total"] <= 8 and (entry["position"] is None or 1 <= entry["position"] <= entry["total"])
    assert detail["rankings"]["games"]["value"] == stats["games"]
    assert detail["rankings"]["avg_deaths"]["higher_is_better"] is False
    assert detail["team_stats"]["team_id"] == stats["team_id"] and detail["team_stats"]["position"] == 1
    assert detail["matches"] and all(MATCH_DETAIL_KEYS <= set(row) for row in detail["matches"])
    played = [row for row in detail["matches"] if not row["is_remake"]]
    assert all(row["largest_multi_kill"] is not None and row["gold_per_min"] > 0 for row in played)


def test_rankings_include_unlinked_and_inactive_player_profile(client: TestClient, admin_headers: dict) -> None:
    mike = _register(client, *MIKE)["player"]
    lea = _register(client, *LEA)["player"]
    sam = _register(client, *SAM)["player"]
    zoe = _register(client, "Zoé")["player"]  # compte non lié
    _create_team(client, admin_headers, {"player_ids": [mike["id"], lea["id"]]})
    client.patch(f"/api/admin/players/{sam['id']}", json={"active": False}, headers=admin_headers)

    data = client.get("/api/rankings").json()
    names = [p["display_name"] for p in data["players"]]
    assert "Sam" not in names and names[-1] == "Zoé"  # inactif exclu ; non lié = non classé, à la fin
    zoe_entry = data["players"][-1]
    assert zoe_entry["position"] is None and zoe_entry["rank_label"] == "Non classé"
    assert zoe_entry["is_linked"] is False and zoe_entry["team_id"] is None
    assert data["tiers"][-1]["tier"] == "UNRANKED" and data["tiers"][-1]["players"] == ["Zoé"]
    assert data["summary"]["unranked_players"] == 1

    # Fiche d'un joueur inactif : stats calculées à part, aucune position
    detail = client.get(f"/api/players/{sam['id']}").json()
    assert detail["stats"]["player_id"] == sam["id"] and detail["team_stats"] is None
    assert all(entry["position"] is None for entry in detail["rankings"].values())
    # Joueur non lié : pas de position non plus ; joueur d'un duo : son duo
    assert all(entry["position"] is None for entry in client.get(f"/api/players/{zoe['id']}").json()["rankings"].values())
    mike_detail = client.get(f"/api/players/{mike['id']}").json()
    assert mike_detail["team_stats"]["team_id"] == mike_detail["stats"]["team_id"]
    assert mike_detail["stats"]["partner"]["player_id"] == lea["id"]
    assert mike_detail["rankings"]["absolute_lp"]["total"] == 2  # Mike et Léa (liés et actifs)
