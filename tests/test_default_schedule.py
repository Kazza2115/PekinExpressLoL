"""Dates par défaut du challenge (10/10/2026 09:00 → 12/10/2026 00:00, heure de Paris)."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import reload_settings
from app.db.models import Challenge, ChallengeStatus
from app.services.bootstrap import apply_default_schedule, parse_fr_datetime
from tests.conftest import ADMIN_PASSWORD

PARIS = ZoneInfo("Europe/Paris")
START = datetime(2026, 10, 10, 7, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 11, 22, 0, tzinfo=timezone.utc)
BEFORE = datetime(2026, 10, 9, 20, 0, tzinfo=timezone.utc)


def test_parse_fr_datetime() -> None:
    assert parse_fr_datetime("10/10/2026 09:00", PARIS) == START
    assert parse_fr_datetime("12/10/2026 00:00", PARIS) == END
    assert parse_fr_datetime("10/10/2026 9h", PARIS) == START
    assert parse_fr_datetime("10.10.2026 09h00", PARIS) == START
    assert parse_fr_datetime("12/10/2026", PARIS) == END
    assert parse_fr_datetime("31/02/2026 10:00", PARIS) is None
    assert parse_fr_datetime("2026-10-10", PARIS) is None and parse_fr_datetime("", PARIS) is None


@pytest.fixture()
def defaults(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CHALLENGE_START", "10/10/2026 09:00")
    monkeypatch.setenv("CHALLENGE_END", "12/10/2026 00:00")
    yield reload_settings()


def test_defaults_applied_once_on_an_empty_challenge(session: Session, defaults) -> None:
    session.add(Challenge(status=ChallengeStatus.REGISTRATION))
    session.commit()
    assert apply_default_schedule(session, now=BEFORE) is True
    challenge = session.get(Challenge, 1)
    assert challenge.start_at.replace(tzinfo=timezone.utc) == START
    assert challenge.end_at.replace(tzinfo=timezone.utc) == END
    # Dates déjà là (saisies ou appliquées) : jamais écrasées
    challenge.start_at = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)
    session.add(challenge)
    session.commit()
    assert apply_default_schedule(session, now=BEFORE) is False


def test_defaults_skipped_when_started_or_past(session: Session, defaults) -> None:
    session.add(Challenge(status=ChallengeStatus.RUNNING))
    session.commit()
    assert apply_default_schedule(session, now=BEFORE) is False
    challenge = session.get(Challenge, 1)
    challenge.status = ChallengeStatus.REGISTRATION
    session.add(challenge)
    session.commit()
    assert apply_default_schedule(session, now=END) is False  # week-end passé : rien


def test_admin_accepts_french_dates(client: TestClient) -> None:
    r = client.patch(
        "/api/admin/challenge",
        headers={"X-Admin-Password": ADMIN_PASSWORD},
        json={"start_at": "10/10/2026 09:00", "end_at": "12/10/2026 00:00"},
    )
    assert r.status_code == 200, r.text
    c = r.json()["challenge"]
    assert datetime.fromisoformat(c["start_at"]) == START and datetime.fromisoformat(c["end_at"]) == END
