"""Tests d'intégration de l'API JSON (TestClient, client Riot démo, poller désactivé)."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from tests.conftest import ADMIN_PASSWORD

MIKE = ("Mike", "La Peace#CHILL")
LEA = ("Léa", "Lea#0001")

MATCH_ROW_KEYS = {
    "match_id", "player_id", "queue", "game_start", "game_duration", "champion_name", "champion_icon_url",
    "position", "win", "kills", "deaths", "assists", "kda", "cs", "cs_per_min", "gold", "damage_to_champions",
    "vision_score", "lp_change", "is_remake", "opgg_url",
}
FEED_EXTRA_KEYS = {"display_name", "team_id", "team_name", "team_color", "ago_s"}
LIVE_KEYS = {
    "player_id", "display_name", "team_id", "team_name", "team_color", "champion_name", "champion_icon_url",
    "game_start", "elapsed_s", "queue_id", "game_mode",
}
PLAYER_PUBLIC_KEYS = {
    "id", "display_name", "riot_id", "game_name", "tag_line", "is_linked", "link_error", "active", "team_id",
    "profile_icon_id", "icon_url", "summoner_level", "tier", "rank", "lp", "rank_label", "rank_color",
    "created_at", "linked_at",
}
POINT_KEYS = {"t", "absolute_lp", "tier", "rank", "lp"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register(client: TestClient, display_name: str, riot_id: str | None = None, expected: int = 201) -> dict:
    body: dict[str, Any] = {"display_name": display_name}
    if riot_id is not None:
        body["riot_id"] = riot_id
    response = client.post("/api/players", json=body)
    assert response.status_code == expected, response.text
    return response.json()


def _admin_post(client: TestClient, headers: dict, path: str, json: Any = None, expected: int = 200) -> dict:
    response = client.post(path, json=json, headers=headers)
    assert response.status_code == expected, response.text
    return response.json()


def _setup_duo(client: TestClient, headers: dict) -> tuple[dict, dict, dict, dict]:
    """Deux joueurs liés → tirage → démarrage du challenge."""
    p1 = _register(client, *MIKE)["player"]
    p2 = _register(client, *LEA)["player"]
    draw = _admin_post(client, headers, "/api/admin/draw")
    started = _admin_post(client, headers, "/api/admin/challenge/start")
    return p1, p2, draw, started


# ---------------------------------------------------------------------------
# Santé et inscription
# ---------------------------------------------------------------------------


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["demo_mode"] is True
    assert data["challenge_status"] == "registration"
    assert data["last_poll"] is None
    assert data["live_count"] == 0


def test_register_linked_and_unlinked(client: TestClient) -> None:
    linked = _register(client, *MIKE)["player"]
    assert PLAYER_PUBLIC_KEYS <= set(linked)
    assert linked["display_name"] == "Mike"
    assert linked["is_linked"] is True
    assert linked["riot_id"] == "La Peace#CHILL"
    assert linked["game_name"] == "La Peace" and linked["tag_line"] == "CHILL"
    assert linked["link_error"] is None
    assert linked["rank_label"] and linked["rank_label"] != "Unranked"
    assert linked["rank_color"].startswith("#")
    assert linked["linked_at"] is not None

    unlinked = _register(client, "Sam")["player"]
    assert unlinked["is_linked"] is False
    assert unlinked["riot_id"] is None
    assert unlinked["rank_label"] == "Unranked"
    assert unlinked["linked_at"] is None

    # Liaison a posteriori
    response = client.post(f"/api/players/{unlinked['id']}/link", json={"riot_id": "Sam#EUW"})
    assert response.status_code == 200, response.text
    player = response.json()["player"]
    assert player["id"] == unlinked["id"]
    assert player["is_linked"] is True
    assert player["game_name"] == "Sam" and player["tag_line"] == "EUW"
    assert player["rank_label"] != "Unranked"


def test_register_duplicate_pseudo(client: TestClient) -> None:
    _register(client, "Mike")
    data = _register(client, "mike", expected=400)  # insensible à la casse
    assert isinstance(data["detail"], str) and data["detail"]


def test_register_invalid_riot_id(client: TestClient) -> None:
    response = client.post("/api/players", json={"display_name": "Bob", "riot_id": "sans-tag"})
    assert response.status_code == 400, response.text
    assert isinstance(response.json()["detail"], str)

    player = _register(client, "Alice")["player"]
    response = client.post(f"/api/players/{player['id']}/link", json={"riot_id": "toujours-sans-tag"})
    assert response.status_code == 400, response.text

    response = client.post("/api/players/9999/link", json={"riot_id": "A#B"})
    assert response.status_code == 404


def test_state_counts(client: TestClient) -> None:
    _register(client, *MIKE)
    _register(client, "Sam")
    data = client.get("/api/state").json()
    assert data["challenge"]["status"] == "registration"
    assert len(data["players"]) == 2
    assert sum(1 for p in data["players"] if p["is_linked"]) == 1
    assert data["teams"] == []
    assert data["demo_mode"] is True
    assert data["games_per_day"] == 10
    assert data["live_count"] == 0
    assert data["last_poll"] is None


# ---------------------------------------------------------------------------
# Admin : authentification, tirage, démarrage
# ---------------------------------------------------------------------------


def test_admin_requires_password(client: TestClient) -> None:
    response = client.post("/api/admin/login")
    assert response.status_code == 401
    assert response.json()["detail"] == "Mot de passe administrateur incorrect."

    response = client.post("/api/admin/login", headers={"X-Admin-Password": "pas-le-bon"})
    assert response.status_code == 401
    response = client.post("/api/admin/draw", headers={"X-Admin-Password": "pas-le-bon"})
    assert response.status_code == 401

    response = client.post("/api/admin/login", headers={"X-Admin-Password": ADMIN_PASSWORD})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_draw_odd_player_count(client: TestClient, admin_headers: dict) -> None:
    _register(client, *MIKE)
    response = client.post("/api/admin/draw", headers=admin_headers)
    assert response.status_code == 400, response.text
    assert isinstance(response.json()["detail"], str)


def test_draw_creates_teams(client: TestClient, admin_headers: dict) -> None:
    p1 = _register(client, *MIKE)["player"]
    p2 = _register(client, *LEA)["player"]
    data = _admin_post(client, admin_headers, "/api/admin/draw")
    assert sorted(data["order"]) == sorted([p1["id"], p2["id"]])
    assert len(data["teams"]) == 1
    team = data["teams"][0]
    assert team["name"] == "Duo Rouge"
    assert team["color"] == "#ef4444"
    assert team["slot"] == 1
    assert sorted(team["player_ids"]) == sorted([p1["id"], p2["id"]])

    state = client.get("/api/state").json()
    assert state["challenge"]["status"] == "drawn"
    assert len(state["teams"]) == 1
    assert sorted(state["teams"][0]["player_ids"]) == sorted([p1["id"], p2["id"]])
    assert all(p["team_id"] == team["id"] for p in state["players"])


def test_start_requires_draw(client: TestClient, admin_headers: dict) -> None:
    _register(client, *MIKE)
    _register(client, *LEA)
    response = client.post("/api/admin/challenge/start", headers=admin_headers)
    assert response.status_code == 400, response.text
    assert response.json()["detail"] == "Tire d'abord les duos."


def test_registration_closed_after_start(client: TestClient, admin_headers: dict) -> None:
    p1, _p2, _draw, _started = _setup_duo(client, admin_headers)
    response = client.post("/api/players", json={"display_name": "Tardif"})
    assert response.status_code == 400
    assert response.json()["detail"] == "Les inscriptions sont closes."
    # Un compte déjà lié ne change plus sans l'organisateur (l'historique de rang serait remplacé)
    response = client.post(f"/api/players/{p1['id']}/link", json={"riot_id": "Autre Compte#EUW"})
    assert response.status_code == 403
    assert "organisateur" in response.json()["detail"]
    # … mais l'organisateur peut le faire
    response = client.post(
        f"/api/players/{p1['id']}/link", json={"riot_id": "Autre Compte#EUW"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["player"]["riot_id"] == "Autre Compte#EUW"


def test_unlinked_player_can_still_link_after_start(client: TestClient, admin_headers: dict) -> None:
    """Un joueur inscrit sans compte (puis désactivé pour le tirage) peut encore se lier."""
    _register(client, *MIKE)
    _register(client, *LEA)
    late = _register(client, "Retardataire")["player"]
    assert late["is_linked"] is False
    response = client.patch(f"/api/admin/players/{late['id']}", json={"active": False}, headers=admin_headers)
    assert response.status_code == 200
    _admin_post(client, admin_headers, "/api/admin/draw")
    _admin_post(client, admin_headers, "/api/admin/challenge/start")
    response = client.post(f"/api/players/{late['id']}/link", json={"riot_id": "Retard#EUW"})
    assert response.status_code == 200, response.text
    assert response.json()["player"]["is_linked"] is True


def test_player_cap(client: TestClient) -> None:
    """Pas plus de MAX_PLAYERS (8) joueurs actifs."""
    for index in range(8):
        _register(client, f"Joueur{index}")
    response = client.post("/api/players", json={"display_name": "Neuvième"})
    assert response.status_code == 400
    assert "places sont prises" in response.json()["detail"]


def test_huge_ids_are_rejected_not_500(client: TestClient, admin_headers: dict) -> None:
    assert client.get(f"/api/players/{2**70}").status_code == 422
    assert client.get(f"/api/players/{2**70}/lp-history").status_code == 422
    assert client.delete(f"/api/admin/players/{2**70}", headers=admin_headers).status_code == 422
    response = client.patch(
        "/api/admin/challenge", json={"start_at": "0001-01-01T00:00"}, headers=admin_headers
    )
    assert response.status_code == 400
    response = client.patch("/api/admin/challenge", json={"games_per_day": 10**23}, headers=admin_headers)
    assert response.status_code == 422


def test_finish_runs_a_last_poll_before_freezing(client: TestClient, admin_headers: dict) -> None:
    _setup_duo(client, admin_headers)
    before = client.get("/api/state").json()["last_poll"]["started_at"]
    finished = _admin_post(client, admin_headers, "/api/admin/challenge/finish")
    assert finished["challenge"]["status"] == "finished"
    after = client.get("/api/state").json()["last_poll"]["started_at"]
    assert after > before  # un cycle de clôture a eu lieu
    assert after <= finished["challenge"]["end_at"]


# ---------------------------------------------------------------------------
# Cycle complet : démarrage → poll → classement → fin → reset
# ---------------------------------------------------------------------------


def test_challenge_flow(client: TestClient, admin_headers: dict) -> None:
    p1, p2, draw, started = _setup_duo(client, admin_headers)
    assert started["challenge"]["status"] == "running"
    assert started["challenge"]["start_at"] is not None
    assert isinstance(started["poll_errors"], list)

    # Cycle forcé
    report = _admin_post(client, admin_headers, "/api/admin/refresh")
    assert report["players_polled"] == 2
    assert isinstance(report["requests"], int)
    assert report["errors"] == []
    for _ in range(2):
        _admin_post(client, admin_headers, "/api/admin/refresh")

    state = client.get("/api/state").json()
    assert state["challenge"]["status"] == "running"
    assert state["last_poll"] is not None and state["last_poll"]["players_polled"] == 2

    # Classement
    board = client.get("/api/leaderboard").json()
    assert board["challenge"]["status"] == "running"
    assert board["generated_at"]
    assert len(board["teams"]) == 1
    team = board["teams"][0]
    assert team["position"] == 1
    assert team["name"] == "Duo Rouge"
    assert len(team["players"]) == 2
    assert isinstance(team["lp_net"], int)
    assert len(board["players"]) == 2
    assert all(isinstance(p["lp_net"], int) for p in board["players"])
    assert all(p["games_limit"] == 10 for p in board["players"])
    for key in ("winrate", "games", "kda"):
        assert client.get(f"/api/leaderboard?sort={key}").status_code == 200
    assert client.get("/api/leaderboard?sort=inconnu").status_code == 400

    # Feed (client démo seedé : chaque joueur joue à chaque tick → des parties sont enregistrées)
    feed = client.get("/api/feed?limit=5").json()
    assert isinstance(feed["items"], list) and 1 <= len(feed["items"]) <= 5
    for item in feed["items"]:
        assert MATCH_ROW_KEYS | FEED_EXTRA_KEYS <= set(item)
        assert item["is_remake"] is False
        assert item["ago_s"] >= 0
        assert item["team_name"] == "Duo Rouge"
        assert item["opgg_url"].startswith("https://www.op.gg/summoners/euw/")
    assert client.get("/api/feed?limit=0").status_code == 200  # borné à 1..100
    assert len(client.get("/api/feed?limit=1000").json()["items"]) <= 100

    # Live
    live = client.get("/api/live").json()
    assert isinstance(live["live"], list)
    for item in live["live"]:
        assert LIVE_KEYS <= set(item)
        assert item["elapsed_s"] >= 0

    # Historique LP global : une courbe par joueur lié, couleur du duo
    history = client.get("/api/lp-history").json()
    assert len(history["series"]) == 2
    for series in history["series"]:
        assert series["color"] == "#ef4444"
        assert series["team_id"] == draw["teams"][0]["id"]
        assert series["points"]
        assert all(POINT_KEYS <= set(point) for point in series["points"])

    # Fiche joueur
    detail = client.get(f"/api/players/{p1['id']}").json()
    assert detail["player"]["id"] == p1["id"]
    assert PLAYER_PUBLIC_KEYS <= set(detail["player"])
    assert detail["team"]["name"] == "Duo Rouge"
    assert detail["stats"]["player_id"] == p1["id"]
    assert isinstance(detail["matches"], list)
    assert all(MATCH_ROW_KEYS <= set(row) for row in detail["matches"])
    assert detail["snapshots"] and all(s["queue"] == "SOLO" for s in detail["snapshots"])
    assert client.get("/api/players/9999").status_code == 404

    points = client.get(f"/api/players/{p1['id']}/lp-history").json()
    assert points["player_id"] == p1["id"]
    assert points["points"] and all(POINT_KEYS <= set(point) for point in points["points"])
    assert client.get("/api/players/9999/lp-history").status_code == 404

    # Renommer un duo
    team_id = draw["teams"][0]["id"]
    response = client.patch(
        f"/api/admin/teams/{team_id}", json={"name": "Les Pandas", "color": "#123456"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["team"]["name"] == "Les Pandas"
    assert response.json()["team"]["color"] == "#123456"
    assert client.get("/api/state").json()["teams"][0]["name"] == "Les Pandas"
    response = client.patch(f"/api/admin/teams/{team_id}", json={"color": "rouge"}, headers=admin_headers)
    assert response.status_code == 400
    response = client.patch("/api/admin/teams/9999", json={"name": "X"}, headers=admin_headers)
    assert response.status_code == 404

    # Modifier le challenge et un joueur
    response = client.patch(
        "/api/admin/challenge", json={"name": "Pékin 2026", "games_per_day": 5}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["challenge"]["games_per_day"] == 5
    assert client.get("/api/state").json()["games_per_day"] == 5
    response = client.patch(f"/api/admin/players/{p2['id']}", json={"display_name": "Léa B"}, headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["player"]["display_name"] == "Léa B"
    response = client.patch(f"/api/admin/players/{p2['id']}", json={"display_name": "mike"}, headers=admin_headers)
    assert response.status_code == 400

    # Fin puis remise à zéro (en gardant les joueurs)
    finished = _admin_post(client, admin_headers, "/api/admin/challenge/finish")
    assert finished["challenge"]["status"] == "finished"
    assert finished["challenge"]["end_at"] is not None

    reset = _admin_post(client, admin_headers, "/api/admin/challenge/reset", json={"keep_players": True})
    assert reset["challenge"]["status"] == "registration"
    assert reset["challenge"]["start_at"] is None and reset["challenge"]["end_at"] is None
    state = client.get("/api/state").json()
    assert len(state["players"]) == 2
    assert state["teams"] == []
    assert all(p["team_id"] is None for p in state["players"])
    assert state["live_count"] == 0
    assert client.get("/api/lp-history").json()["series"] == []
    assert client.get("/api/feed").json()["items"] == []
    assert client.get("/api/leaderboard").json()["teams"] == []


def test_reset_drops_players(client: TestClient, admin_headers: dict) -> None:
    _register(client, *MIKE)
    _register(client, *LEA)
    _admin_post(client, admin_headers, "/api/admin/challenge/reset", json={"keep_players": False})
    assert client.get("/api/state").json()["players"] == []


def test_delete_player(client: TestClient, admin_headers: dict) -> None:
    player = _register(client, *MIKE)["player"]
    response = client.delete(f"/api/admin/players/{player['id']}", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["deleted_id"] == player["id"]
    assert client.get(f"/api/players/{player['id']}").status_code == 404
    assert client.delete("/api/admin/players/9999", headers=admin_headers).status_code == 404


# ---------------------------------------------------------------------------
# Démo, maintenance, SSE
# ---------------------------------------------------------------------------


def test_demo_fill(client: TestClient) -> None:
    _register(client, *MIKE)
    response = client.post("/api/demo/fill")
    assert response.status_code == 200, response.text
    players = response.json()["players"]
    assert len(players) == 8
    assert all(p["is_linked"] for p in players)
    assert any(p["display_name"] == "Mike" for p in players)
    # Idempotent
    assert len(client.post("/api/demo/fill").json()["players"]) == 8


def test_admin_maintenance(client: TestClient, admin_headers: dict) -> None:
    response = client.post("/api/admin/test-notification", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"sent": False}  # pas de webhook configuré en test

    response = client.post("/api/admin/reload-settings", headers=admin_headers)
    assert response.status_code == 200
    # Même mode (démo) → le client simulé est conservé (sinon les rangs repartiraient de zéro)
    assert response.json() == {"demo_mode": True, "has_api_key": False, "client_replaced": False}


def test_sse_does_not_hold_a_db_connection(client: TestClient) -> None:
    """Le flux SSE ne garde pas de session SQLAlchemy ouverte (un onglet = zéro connexion du pool)."""
    import inspect

    from app.api import routes_api

    params = inspect.signature(routes_api.get_events).parameters
    assert "session" not in params and "challenge" not in params


def test_deactivating_a_live_player_publishes_live_end(client: TestClient, admin_headers: dict) -> None:
    from datetime import datetime, timezone

    from app.events import bus
    from app.state import LiveGameState, state

    player = _register(client, *MIKE)["player"]
    now = datetime.now(timezone.utc)
    state.live_games[player["id"]] = LiveGameState(
        player_id=player["id"], game_id=1, champion_id=103, champion_name="Ahri", queue_id=420,
        game_mode="CLASSIC", game_start=now, detected_at=now,
    )
    response = client.patch(f"/api/admin/players/{player['id']}", json={"active": False}, headers=admin_headers)
    assert response.status_code == 200
    assert player["id"] not in state.live_games
    ends = [e for e in bus.recent(50) if e["type"] == "live_end"]
    assert ends and ends[-1]["data"]["player_id"] == player["id"]


def test_sse_stream_events(engine, monkeypatch) -> None:
    """Le générateur SSE lui-même : hello, ping à vide, événements `id/event/data`, rejeu via `since`."""
    import asyncio

    from app.api import routes_api
    from app.events import bus

    monkeypatch.setattr(routes_api, "PING_INTERVAL_S", 0.05)
    ids: list[int] = []  # le compteur du bus n'est pas remis à zéro entre les tests

    async def scenario() -> list[str]:
        chunks: list[str] = []
        stream = routes_api._event_stream(None, {"live": []}, 3)
        chunks.append(await stream.__anext__())  # hello
        chunks.append(await stream.__anext__())  # ping (rien publié)
        ids.append(bus.publish("live_start", {"player_id": 1, "champion_name": "Ahri"})["id"])
        chunks.append(await stream.__anext__())
        ids.append(bus.publish("rank_changed", {"player_id": 1})["id"])
        chunks.append(await stream.__anext__())  # 3e événement → le flux se ferme
        try:
            await stream.__anext__()
        except StopAsyncIteration:
            chunks.append("<closed>")
        # Rejeu de l'historique après `since` = premier événement → on reçoit le second
        replay = routes_api._event_stream(ids[0], {}, 2)
        await replay.__anext__()
        chunks.append(await replay.__anext__())
        await replay.aclose()
        await asyncio.sleep(0.01)
        chunks.append(f"<subscribers={bus.subscriber_count}>")
        return chunks

    chunks = asyncio.run(scenario())
    first, second = ids
    assert chunks[0] == 'event: hello\ndata: {"live": []}\n\n'
    assert chunks[1] == ": ping\n\n"
    assert chunks[2] == f'id: {first}\nevent: live_start\ndata: {{"player_id": 1, "champion_name": "Ahri"}}\n\n'
    assert chunks[3] == f'id: {second}\nevent: rank_changed\ndata: {{"player_id": 1}}\n\n'
    assert chunks[4] == "<closed>"
    assert chunks[5] == f'id: {second}\nevent: rank_changed\ndata: {{"player_id": 1}}\n\n'
    assert chunks[6] == "<subscribers=0>"


def test_sse_hello(client: TestClient) -> None:
    # `max_events=1` : le flux se ferme juste après `hello` (le TestClient attend la fin de la réponse)
    with client.stream("GET", "/api/events?max_events=1") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers.get("cache-control") == "no-cache"
        received = b""
        for chunk in response.iter_bytes():
            received += chunk
            if b"\n\n" in received:
                break
    assert received.startswith(b"event: hello\n")
    assert b'"challenge_status": "registration"' in received
    assert b'"live": []' in received
