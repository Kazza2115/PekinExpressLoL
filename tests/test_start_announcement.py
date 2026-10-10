"""Annonce Discord « c'est parti » à l'heure du début du challenge (GIF choisi, duos, rôle)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.config import DEFAULT_START_GIF
from app.db.models import Challenge, ChallengeStatus, Player, Team, utcnow
from app.db.session import as_utc
from app.events import bus
from app.services import announce, gifs, notifications
from tests.test_discord_embeds import make_settings

pytestmark = pytest.mark.anyio

GIF = "https://static.klipy.com/ii/sponge.gif"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _fresh_gif_cache():
    gifs.reset_cache()
    yield
    gifs.reset_cache()


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, list[dict[str, Any]]]]:
    messages: list[tuple[str, list[dict[str, Any]]]] = []

    async def fake_post(content: str, *, embeds=None, **_: Any) -> notifications.DiscordSendResult:
        messages.append((content, embeds or []))
        return notifications.DiscordSendResult(sent=True, status=204)

    async def fake_resolve(url: str | None, **_: Any) -> str | None:
        return GIF if url else None

    monkeypatch.setattr(notifications, "post_discord", fake_post)
    monkeypatch.setattr(gifs, "resolve_gif", fake_resolve)
    return messages


def setup_challenge(session: Session, *, status=ChallengeStatus.RUNNING, start_in=timedelta(minutes=-1)) -> Challenge:
    challenge = Challenge(status=status, start_at=utcnow() + start_in, end_at=utcnow() + timedelta(days=1))
    session.add(challenge)
    team = Team(name="Duo Rouge", color="#ef4444", slot=1)
    session.add(team)
    session.commit()
    session.refresh(team)
    session.add(Player(display_name="Mike", team_id=team.id))
    session.add(Player(display_name="Léa", team_id=team.id))
    session.commit()
    session.refresh(challenge)
    return challenge


async def test_announces_once_at_start_time_with_gif_and_duos(session: Session, sent):
    challenge = setup_challenge(session)
    settings = make_settings(gif_start=DEFAULT_START_GIF)
    assert await announce.announce_start_if_due(settings=settings) is True
    assert await announce.announce_start_if_due(settings=settings) is False  # une seule fois
    assert len(sent) == 1
    content, embeds = sent[0]
    assert "c'est parti" in content
    embed = embeds[0]
    assert embed["image"] == {"url": GIF}
    assert {"name": "Duo Rouge", "value": "Mike & Léa", "inline": True} in embed["fields"]
    assert "10 parties par jour" in embed["description"] and "Fin :" in embed["description"]
    session.refresh(challenge)
    assert challenge.start_announced_at is not None
    assert any(e["type"] == "challenge_begins" for e in bus.recent(limit=50))


async def test_nothing_before_start_or_when_not_started(session: Session, sent):
    setup_challenge(session, start_in=timedelta(minutes=5))
    settings = make_settings(gif_start=DEFAULT_START_GIF)
    assert await announce.announce_start_if_due(settings=settings) is False
    challenge = session.get(Challenge, 1)
    challenge.status = ChallengeStatus.DRAWN  # « Démarrer » pas cliqué : pas d'annonce
    challenge.start_at = utcnow() - timedelta(minutes=1)
    session.add(challenge)
    session.commit()
    assert await announce.announce_start_if_due(settings=settings) is False
    assert sent == []


async def test_too_late_is_dropped_and_failed_send_is_retried(session: Session, sent, monkeypatch: pytest.MonkeyPatch):
    challenge = setup_challenge(session, start_in=-announce.ANNOUNCE_MAX_DELAY - timedelta(minutes=1))
    settings = make_settings(gif_start=DEFAULT_START_GIF)
    assert await announce.announce_start_if_due(settings=settings) is False and sent == []
    session.refresh(challenge)
    assert challenge.start_announced_at is not None  # abandonnée : plus d'essai

    # Début reprogrammé plus tard → nouvelle annonce à la nouvelle heure ; Discord refuse d'abord → nouvel essai
    challenge.start_at = utcnow() + timedelta(minutes=10)
    session.add(challenge)
    session.commit()
    later = utcnow() + timedelta(minutes=11)
    calls = {"n": 0}

    async def flaky_post(content: str, **_: Any) -> notifications.DiscordSendResult:
        calls["n"] += 1
        sent.append((content, []))
        return notifications.DiscordSendResult(sent=calls["n"] > 1, error=None if calls["n"] > 1 else "HTTP 500")

    monkeypatch.setattr(notifications, "post_discord", flaky_post)
    assert await announce.announce_start_if_due(settings=settings) is False  # pas encore l'heure
    assert calls["n"] == 0
    assert await announce.announce_start_if_due(settings=settings, now=later) is False  # refusé
    assert await announce.announce_start_if_due(settings=settings, now=later) is True
    assert await announce.announce_start_if_due(settings=settings, now=later) is False
    assert calls["n"] == 2


async def test_without_resolved_gif_the_page_link_is_in_the_message():
    content, embeds = notifications.build_start_announcement(
        "Pékin Express LoL", 10, None, [("Duo Rouge", ["Mike", "Léa"])], gif=None, gif_page=DEFAULT_START_GIF,
        settings=make_settings(),
    )
    assert DEFAULT_START_GIF in content and "image" not in embeds[0]  # Discord affiche le GIF du lien


async def test_resolve_klipy_page_with_api_key_then_without():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "api.klipy.com":
            return httpx.Response(200, json={"result": True, "data": {"data": [{"type": "gif", "file": {"md": {"gif": {"url": GIF}}}}]}})
        html = '<html><head><meta property="og:image" content="https://static.klipy.com/ii/sponge.webp"></head></html>'
        return httpx.Response(200, text=html)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with_key = await gifs.resolve_gif(DEFAULT_START_GIF, settings=make_settings(klipy_api_key="cle"), client=client)
        assert with_key == GIF and seen[0].url.params["slugs"] == "sponge-bob-bob-esponja"
        gifs.reset_cache()
        without_key = await gifs.resolve_gif(DEFAULT_START_GIF, settings=make_settings(), client=client)
        assert without_key == "https://static.klipy.com/ii/sponge.webp"  # image annoncée par la page
        assert await gifs.resolve_gif("https://media.tenor.com/x/y.gif", settings=make_settings(), client=client) == "https://media.tenor.com/x/y.gif"
        assert await gifs.resolve_gif("", settings=make_settings(), client=client) is None


def test_admin_start_now_announces_and_future_start_says_ready(client: TestClient, admin_headers: dict, monkeypatch: pytest.MonkeyPatch):
    from app.api import routes_admin

    calls: list[str] = []

    async def fake_announce(**_: Any) -> bool:
        calls.append("announce")
        return True

    async def fake_send(content: str, **_: Any) -> bool:
        calls.append(content)
        return True

    monkeypatch.setattr(routes_admin, "announce_start_if_due", fake_announce)
    monkeypatch.setattr(notifications, "send_discord", fake_send)
    players = client.post("/api/demo/fill").json()["players"]
    assert len(players) == 8
    assert client.post("/api/admin/draw", headers=admin_headers).status_code == 200
    future = (utcnow() + timedelta(hours=2)).isoformat()
    assert client.post("/api/admin/challenge/start", headers=admin_headers, json={"start_at": future}).status_code == 200
    assert calls and calls[-1].startswith("🚀") and "tout est prêt" in calls[-1]
    assert client.post("/api/admin/challenge/finish", headers=admin_headers).status_code == 200
    calls.clear()
    assert client.post("/api/admin/challenge/start", headers=admin_headers, json={}).status_code == 200
    assert calls == ["announce"]  # début immédiat : l'annonce « c'est parti » (GIF, duos)


async def test_no_webhook_keeps_the_announcement_pending(session: Session, sent):
    challenge = setup_challenge(session)
    no_hook = make_settings(gif_start=DEFAULT_START_GIF, discord_webhook_url="")
    assert await announce.announce_start_if_due(settings=no_hook) is False
    session.refresh(challenge)
    assert challenge.start_announced_at is None  # webhook ajouté ensuite : l'annonce partira
    assert announce.announce_status(settings=no_hook)["state"] == "no_webhook"
    assert await announce.announce_start_if_due(settings=make_settings(gif_start=DEFAULT_START_GIF)) is True


async def test_status_explains_why_and_manual_send_always_works(session: Session, sent):
    challenge = setup_challenge(session, start_in=-timedelta(hours=14))  # « Démarrer » cliqué la veille
    settings = make_settings(gif_start=DEFAULT_START_GIF)
    assert await announce.announce_start_if_due(settings=settings) is False
    status = announce.announce_status(settings=settings)
    assert status["state"] == "dropped" and "plus de 3 h" in status["message"] and status["start_at"]
    result = await announce.announce_start_now(settings=settings)
    assert result["sent"] is True and len(sent) == 1 and result["after"]["state"] == "sent"
    challenge.status = ChallengeStatus.DRAWN
    session.add(challenge)
    session.commit()
    assert announce.announce_status(settings=settings)["state"] == "not_running"


def test_admin_announce_routes(client: TestClient, admin_headers: dict, sent):
    assert client.get("/api/admin/announce-start").status_code == 401
    status = client.get("/api/admin/announce-start", headers=admin_headers).json()
    assert status["state"] == "not_running"
    result = client.post("/api/admin/announce-start", headers=admin_headers, json={}).json()
    assert set(result) == {"sent", "error", "before", "after"}
