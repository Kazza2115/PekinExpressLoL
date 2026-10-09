"""Calendrier réel du challenge : samedi 10/10/2026 9h → nuit de dimanche à lundi 00h (Paris)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api import routes_admin
from app.db.models import Challenge, ChallengeStatus, Match, MatchParticipant, Queue
from app.events import bus
from app.services import poller as poller_module
from app.services.poller import Poller
from app.state import state
from tests.conftest import ADMIN_PASSWORD
from tests.test_poller import ScriptedAPI, events_of

H = {"X-Admin-Password": ADMIN_PASSWORD}
START_UTC = datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc)  # samedi 9h à Paris (UTC+2)
END_UTC = datetime(2026, 10, 11, 22, 0, tzinfo=timezone.utc)  # lundi 00h à Paris



def parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def setup_duo(client: TestClient) -> list[int]:
    ids = []
    for name, riot in (("Mike", "Mike Demo#EUW"), ("Léa", "Lea Demo#EUW")):
        r = client.post("/api/players", json={"display_name": name, "riot_id": riot})
        assert r.status_code == 201, r.text
        ids.append(r.json()["player"]["id"])
    r = client.post("/api/admin/teams", headers=H, json={"player_ids": ids})
    assert r.status_code == 201, r.text
    return ids


def add_game(session: Session, player_id: int, match_id: str, end_utc: datetime) -> None:
    start = end_utc - timedelta(minutes=30)
    session.add(Match(match_id=match_id, queue_id=420, game_start=start, game_end=end_utc, game_duration=1800, raw_json="{}"))
    session.add(MatchParticipant(
        match_id=match_id, player_id=player_id, queue=Queue.SOLO, game_start=start, game_end=end_utc,
        game_duration=1800, champion_name="Ahri", win=True,
    ))
    session.commit()


@pytest.mark.parametrize(
    "start_value,end_value",
    [
        ("2026-10-10T09:00", "2026-10-12T00:00"),  # heure de Paris (saisie simple)
        ("2026-10-10T07:00:00.000Z", "2026-10-11T22:00:00.000Z"),  # ce qu'envoie le navigateur (Admin)
    ],
)
def test_planned_dates_survive_start_and_bound_the_games(client: TestClient, session: Session, start_value, end_value):
    ids = setup_duo(client)
    r = client.patch("/api/admin/challenge", headers=H, json={"start_at": start_value, "end_at": end_value})
    assert r.status_code == 200, r.text
    assert parse(r.json()["challenge"]["start_at"]) == START_UTC
    assert parse(r.json()["challenge"]["end_at"]) == END_UTC

    # « Démarrer » cliqué la veille : les dates programmées sont gardées
    r = client.post("/api/admin/challenge/start", headers=H, json={})
    assert r.status_code == 200, r.text
    challenge = r.json()["challenge"]
    assert challenge["status"] == "running"
    assert parse(challenge["start_at"]) == START_UTC and parse(challenge["end_at"]) == END_UTC

    mike = ids[0]
    add_game(session, mike, "EUW1_BEFORE", START_UTC - timedelta(minutes=1))  # samedi 8h59 : ne compte pas
    add_game(session, mike, "EUW1_SAT", START_UTC + timedelta(minutes=30))  # samedi 9h30
    add_game(session, mike, "EUW1_SUN", END_UTC - timedelta(minutes=10))  # dimanche 23h50
    add_game(session, mike, "EUW1_AFTER", END_UTC + timedelta(minutes=10))  # lundi 00h10 : ne compte pas
    stats = client.get(f"/api/players/{mike}").json()["stats"]
    assert stats["games"] == 2
    assert stats["games_per_day"] == {"2026-10-10": 1, "2026-10-11": 1}


def test_finish_after_the_planned_end_keeps_it(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    setup_duo(client)
    client.patch("/api/admin/challenge", headers=H, json={"start_at": "2026-10-10T09:00", "end_at": "2026-10-12T00:00"})
    assert client.post("/api/admin/challenge/start", headers=H, json={}).status_code == 200

    # « Terminer » cliqué lundi à 10h : la fin reste lundi 00h
    monkeypatch.setattr(routes_admin, "utcnow", lambda: END_UTC + timedelta(hours=10))
    r = client.post("/api/admin/challenge/finish", headers=H)
    assert r.status_code == 200, r.text
    assert parse(r.json()["challenge"]["end_at"]) == END_UTC

    # Redémarrer après un challenge terminé : les anciennes dates ne sont pas reprises
    later = END_UTC + timedelta(days=7)
    monkeypatch.setattr(routes_admin, "utcnow", lambda: later)
    r = client.post("/api/admin/challenge/start", headers=H, json={})
    assert parse(r.json()["challenge"]["start_at"]) == later and r.json()["challenge"]["end_at"] is None


def test_finish_before_the_planned_end_ends_now(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    setup_duo(client)
    client.patch("/api/admin/challenge", headers=H, json={"start_at": "2026-10-10T09:00", "end_at": "2026-10-12T00:00"})
    client.post("/api/admin/challenge/start", headers=H, json={})
    early = START_UTC + timedelta(hours=5)
    monkeypatch.setattr(routes_admin, "utcnow", lambda: early)
    r = client.post("/api/admin/challenge/finish", headers=H)
    assert parse(r.json()["challenge"]["end_at"]) == early


def test_end_before_start_is_ignored_with_a_warning(client: TestClient):
    setup_duo(client)
    client.patch("/api/admin/challenge", headers=H, json={"start_at": "2026-10-10T09:00", "end_at": "2026-10-10T08:00"})
    r = client.post("/api/admin/challenge/start", headers=H, json={})
    assert r.json()["challenge"]["end_at"] is None
    assert any("fin enregistrée" in w for w in r.json()["warnings"])


@pytest.mark.anyio
async def test_automatic_finish_15_minutes_after_the_planned_end(session: Session, monkeypatch: pytest.MonkeyPatch):
    session.add(Challenge(status=ChallengeStatus.RUNNING, start_at=START_UTC, end_at=END_UTC))
    session.commit()
    poller = Poller(ScriptedAPI(), bus, state)

    monkeypatch.setattr(poller_module, "_utcnow", lambda: END_UTC + timedelta(minutes=5))
    await poller.poll_once()
    assert events_of("challenge_finished") == []

    monkeypatch.setattr(poller_module, "_utcnow", lambda: END_UTC + timedelta(minutes=16))
    await poller.poll_once()
    finished = events_of("challenge_finished")
    assert len(finished) == 1 and finished[0]["data"]["automatic"] is True
    session.expire_all()
    challenge = session.get(Challenge, 1)
    assert challenge.status == ChallengeStatus.FINISHED
    assert challenge.end_at.replace(tzinfo=timezone.utc) == END_UTC  # la fin reste celle programmée

    await poller.poll_once()  # déjà terminé : pas de seconde annonce
    assert len(events_of("challenge_finished")) == 1
