"""Détection rapide des parties en cours (notifications) : boucle Spectator entre deux cycles,
diagnostic Admin « Qui est en game ? », service worker des notifications."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import Settings, reload_settings
from app.db.models import ChallengeStatus, utcnow
from app.riot.base import ActiveGameDTO, RiotUnauthorized
from app.services import poller as poller_module
from app.services.poller import Poller
from app.state import state
from tests.test_poller import ScriptedAPI, events_of, make_challenge, make_player


def _settings(poll: int = 90, live: int = 30) -> Settings:
    return Settings(
        riot_api_key="RGAPI-test",
        riot_platform="euw1",
        riot_region="europe",
        demo_mode=False,
        poll_interval_seconds=poll,
        track_flex=False,
        admin_password="x",
        database_url="sqlite://",
        games_per_day=10,
        max_players=8,
        timezone="Europe/Paris",
        discord_webhook_url="",
        base_url="http://localhost:8000",
        live_poll_seconds=live,
    )


def _ahri(game_id: int = 1, queue_id: int = 420) -> ActiveGameDTO:
    return ActiveGameDTO(
        game_id=game_id, game_start=utcnow(), queue_id=queue_id, game_mode="CLASSIC", champion_id=103, champion_name="Ahri"
    )


# --------------------------------------------------------------------------- #
# Réglage
# --------------------------------------------------------------------------- #


def test_live_poll_seconds_default_and_minimum(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVE_POLL_SECONDS", raising=False)
    assert reload_settings().live_poll_seconds == 30
    monkeypatch.setenv("LIVE_POLL_SECONDS", "3")
    assert reload_settings().live_poll_seconds == 10  # minimum : budget d'une clé de dev
    monkeypatch.setenv("LIVE_POLL_SECONDS", "45")
    assert reload_settings().live_poll_seconds == 45
    os.environ.pop("LIVE_POLL_SECONDS", None)
    reload_settings()


# --------------------------------------------------------------------------- #
# poll_live_once
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_live_pass_announces_start_and_end_without_full_cycle(session: Session) -> None:
    make_challenge(session, ChallengeStatus.RUNNING)
    mike = make_player(session, "Mike", "p-mike")
    make_player(session, "Léa", "p-lea")
    api = ScriptedAPI()
    api.active["p-mike"] = _ahri()
    poller = Poller(api, poller_module_bus(), state, settings=_settings())

    result = await poller.poll_live_once()
    assert result["skipped"] is False
    assert api.request_count == 2  # Spectator seul : 1 requête par joueur, ni League ni Match-V5
    starts = events_of("live_start")
    assert len(starts) == 1 and starts[0]["data"]["display_name"] == "Mike"
    assert starts[0]["data"]["ranked"] is True
    assert events_of("poll_done") == []  # pas de rechargement des pages toutes les 30 s
    assert state.last_live_check is not None
    by_name = {p["display_name"]: p for p in result["players"]}
    assert by_name["Mike"]["in_game"] is True and by_name["Mike"]["champion_name"] == "Ahri"
    assert by_name["Mike"]["ranked"] is True and by_name["Mike"]["error"] is None
    assert by_name["Léa"]["in_game"] is False

    # Même partie au passage suivant : pas de nouvelle annonce
    await poller.poll_live_once()
    assert len(events_of("live_start")) == 1

    # Partie terminée
    api.active["p-mike"] = None
    await poller.poll_live_once()
    ends = events_of("live_end")
    assert len(ends) == 1 and ends[0]["data"]["player_id"] == mike.id


def poller_module_bus():
    from app.events import bus

    return bus


@pytest.mark.anyio
async def test_live_pass_reports_errors_per_player(session: Session) -> None:
    make_challenge(session, ChallengeStatus.REGISTRATION)
    make_player(session, "Mike", "p-mike")
    make_player(session, "Léa", "p-lea")
    api = ScriptedAPI()

    async def refused(puuid: str) -> Any:
        api.request_count += 1
        raise RiotUnauthorized("Riot 403", status=403)

    api.get_active_game = refused  # type: ignore[method-assign]
    result = await Poller(api, poller_module_bus(), state, settings=_settings()).poll_live_once()
    assert api.request_count == 1  # clé refusée : on n'insiste pas pour Léa
    mike, lea = result["players"]
    assert "Clé Riot" in mike["error"] or "clé" in mike["error"].lower()
    assert lea["error"] == "cycle interrompu (voir l'erreur précédente)"
    assert result["errors"]


@pytest.mark.anyio
async def test_live_pass_skips_while_full_cycle_runs_unless_waiting(session: Session) -> None:
    make_challenge(session, ChallengeStatus.REGISTRATION)
    make_player(session, "Mike", "p-mike")
    api = ScriptedAPI()
    poller = Poller(api, poller_module_bus(), state, settings=_settings())

    async with poller._lock:  # noqa: SLF001 — un cycle complet est en cours
        assert (await poller.poll_live_once())["skipped"] is True
        waiting = asyncio.create_task(poller.poll_live_once(wait=True))
        await asyncio.sleep(0)
        assert not waiting.done()
    result = await waiting
    assert result["skipped"] is False and len(result["players"]) == 1


# --------------------------------------------------------------------------- #
# Attente entre deux cycles
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_wait_runs_live_checks_between_full_cycles(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 0.0}
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(round(seconds, 3))
        clock["t"] += seconds

    monkeypatch.setattr(poller_module.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(poller_module.asyncio, "sleep", fake_sleep)
    poller = Poller(ScriptedAPI(), poller_module_bus(), state, settings=_settings(poll=90, live=30))
    live_calls: list[float] = []

    async def fake_live(**_: Any) -> dict:
        live_calls.append(clock["t"])
        return {}

    poller.poll_live_once = fake_live  # type: ignore[method-assign]
    await poller._wait_for_next_cycle()  # noqa: SLF001
    # Vérifications à t = 30 s et 60 s ; le cycle complet de t = 90 s fait la troisième
    assert live_calls == [30.0, 60.0]
    assert sum(sleeps) == pytest.approx(90.0)

    # Détection rapide inutile si elle n'est pas plus fréquente que le cycle complet
    live_calls.clear()
    sleeps.clear()
    poller._settings = _settings(poll=20, live=30)  # noqa: SLF001
    await poller._wait_for_next_cycle()  # noqa: SLF001
    assert live_calls == [] and sleeps == [20]


@pytest.mark.anyio
async def test_wait_survives_live_check_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(poller_module.asyncio, "sleep", fake_sleep)
    clock = {"t": 0.0}

    def tick() -> float:
        clock["t"] += 15.0
        return clock["t"]

    monkeypatch.setattr(poller_module.time, "monotonic", tick)
    poller = Poller(ScriptedAPI(), poller_module_bus(), state, settings=_settings(poll=90, live=10))

    async def broken(**_: Any) -> dict:
        raise RuntimeError("boom")

    poller.poll_live_once = broken  # type: ignore[method-assign]
    await poller._wait_for_next_cycle()  # noqa: SLF001 — ne lève pas


# --------------------------------------------------------------------------- #
# API : diagnostic Admin, état, service worker
# --------------------------------------------------------------------------- #


def test_admin_live_check(client: TestClient, admin_headers: dict) -> None:
    assert client.post("/api/admin/live-check").status_code in (401, 403)
    for name, riot_id in (("Mike", "MikeDemo#EUW"), ("Léa", "LeaDemo#EUW")):
        assert client.post("/api/players", json={"display_name": name, "riot_id": riot_id}).status_code == 201
    response = client.post("/api/admin/live-check", headers=admin_headers, json={})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["skipped"] is False and body["checked_at"]
    assert {p["display_name"] for p in body["players"]} == {"Mike", "Léa"}
    for player in body["players"]:
        assert set(player) >= {"player_id", "display_name", "riot_id", "in_game", "champion_name", "ranked", "error"}
    assert body["live_poll_seconds"] >= 10 and body["poll_interval_seconds"] >= 3
    assert body["key_hint"] is None  # pas de clé en test ; jamais la clé entière
    # Le client démo des tests lance une partie à chaque passage : la notification part
    if any(p["in_game"] for p in body["players"]):
        assert events_of("live_start")

    state_body = client.get("/api/state").json()
    assert state_body["last_live_check"] == body["checked_at"]
    assert state_body["live_poll_seconds"] == body["live_poll_seconds"]


def test_service_worker_is_served_at_root(client: TestClient) -> None:
    response = client.get("/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/javascript")
    assert response.headers["cache-control"] == "no-cache"
    assert "notificationclick" in response.text
    assert "fetch" not in response.text.replace("aucune interception réseau", "")  # jamais de cache


def test_key_hint_shows_only_the_last_characters(client: TestClient, admin_headers: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-12345678-abcd-ef01-2345-6789abcd1a2b")
    monkeypatch.setenv("DEMO_MODE", "1")
    reload_settings()
    body = client.post("/api/admin/live-check", headers=admin_headers, json={}).json()
    assert body["key_hint"] == "…1a2b"
    assert "RGAPI-1234" not in client.get("/api/state").text
