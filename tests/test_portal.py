"""Lien fixe GitHub Pages : publication de l'adresse du site dans docs/site.json."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.services import portal, tunnel
from tests.test_tunnel import LOG, make_settings

TOKEN = "github_pat_SECRET_1234567890"
ENDPOINT = "https://api.github.com/repos/Kazza2115/PekinExpressLoL/contents/docs/site.json"


def settings(**overrides):
    base = replace(make_settings("http://localhost:8000"), github_token=TOKEN)
    return replace(base, **overrides)


class FakeGitHub:
    def __init__(self, existing_url: str | None = None, *, get_status: int = 200, put_statuses: list[int] | None = None):
        self.existing_url = existing_url
        self.get_status = get_status
        self.put_statuses = list(put_statuses or [201])
        self.puts: list[dict] = []
        self.headers: list[httpx.Headers] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.headers.append(request.headers)
        assert str(request.url).startswith(ENDPOINT)
        if request.method == "GET":
            assert request.url.params["ref"] == "claude/quirky-pascal-y84evb"
            if self.existing_url is None or self.get_status != 200:
                return httpx.Response(404 if self.existing_url is None else self.get_status, json={})
            content = base64.b64encode(json.dumps({"url": self.existing_url}).encode()).decode()
            return httpx.Response(200, json={"sha": "abc123", "content": content})
        body = json.loads(request.content)
        self.puts.append(body)
        return httpx.Response(self.put_statuses.pop(0) if self.put_statuses else 201, json={})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


@pytest.fixture(autouse=True)
def _reset_state():
    portal.portal_state.__init__()
    portal._first_seen.clear()  # noqa: SLF001
    yield
    portal.portal_state.__init__()
    portal._first_seen.clear()  # noqa: SLF001


async def answers(url: str) -> bool:
    return True


async def silent(url: str) -> bool:
    return False


def test_portal_page_url() -> None:
    assert portal.portal_page_url("Kazza2115/PekinExpressLoL") == "https://kazza2115.github.io/PekinExpressLoL/"


@pytest.mark.anyio
async def test_publish_creates_then_skips_when_unchanged() -> None:
    gh = FakeGitHub(existing_url=None)
    async with gh.client() as client:
        assert await portal.publish_site_url("https://abc.trycloudflare.com", settings=settings(), client=client) is True
    put = gh.puts[0]
    assert put["branch"] == "claude/quirky-pascal-y84evb" and "sha" not in put
    assert json.loads(base64.b64decode(put["content"]))["url"] == "https://abc.trycloudflare.com"
    assert gh.headers[0]["authorization"] == f"Bearer {TOKEN}"

    same = FakeGitHub(existing_url="https://abc.trycloudflare.com")
    async with same.client() as client:
        assert await portal.publish_site_url("https://abc.trycloudflare.com", settings=settings(), client=client) is False
    assert same.puts == []

    changed = FakeGitHub(existing_url="https://old.trycloudflare.com")
    async with changed.client() as client:
        assert await portal.publish_site_url("https://new.trycloudflare.com", settings=settings(), client=client) is True
    assert changed.puts[0]["sha"] == "abc123"


@pytest.mark.anyio
async def test_publish_errors_are_french_and_never_leak_the_token() -> None:
    refused = FakeGitHub(existing_url="https://old.trycloudflare.com", get_status=401)
    async with refused.client() as client:
        with pytest.raises(portal.PortalError) as exc:
            await portal.publish_site_url("https://new.trycloudflare.com", settings=settings(), client=client)
    assert "Jeton GitHub refusé" in str(exc.value) and TOKEN not in str(exc.value)

    conflict = FakeGitHub(existing_url="https://old.trycloudflare.com", put_statuses=[409, 200])
    async with conflict.client() as client:
        assert await portal.publish_site_url("https://new.trycloudflare.com", settings=settings(), client=client) is True
    assert len(conflict.puts) == 2  # relu puis réécrit

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
        with pytest.raises(portal.PortalError, match="GitHub injoignable"):
            await portal.publish_site_url("https://new.trycloudflare.com", settings=settings(), client=client)


@pytest.mark.anyio
async def test_sync_publishes_tunnel_url_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "tunnel.log"
    log.write_text(LOG, encoding="utf-8")
    monkeypatch.setattr(tunnel, "TUNNEL_LOG", log)
    monkeypatch.setattr(portal, "public_url", lambda s: tunnel.public_url(s, log))
    gh = FakeGitHub(existing_url=None)
    async with gh.client() as client:
        await portal.sync_portal_once(settings(), client, probe=answers)
        await portal.sync_portal_once(settings(), client, probe=answers)  # même adresse : pas de nouvel envoi
    assert len(gh.puts) == 1
    state = portal.portal_state
    assert state.enabled and state.error is None
    assert state.published_url == "https://rec-explained-donation-frequency.trycloudflare.com"
    assert portal.share_url(settings()) == "https://kazza2115.github.io/PekinExpressLoL"

    # Sans jeton : rien n'est envoyé, le partage garde l'adresse du tunnel
    portal.portal_state.__init__()
    quiet = FakeGitHub()
    async with quiet.client() as client:
        await portal.sync_portal_once(settings(github_token=""), client)
    assert quiet.puts == [] and portal.portal_state.enabled is False
    assert portal.share_url(settings(github_token="")).endswith(".trycloudflare.com")


@pytest.mark.anyio
async def test_sync_waits_for_a_public_address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portal, "public_url", lambda s: tunnel.public_url(s, tmp_path / "absent.log"))
    gh = FakeGitHub()
    async with gh.client() as client:
        await portal.sync_portal_once(settings(), client)
    assert gh.puts == [] and portal.portal_state.published_url is None


def test_state_exposes_portal_without_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import reload_settings

    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)
    reload_settings()
    response = client.get("/api/state")
    body = response.json()["portal"]
    assert body["enabled"] is True and body["portal_url"] == "https://kazza2115.github.io/PekinExpressLoL/"
    assert TOKEN not in response.text


def test_pages_entry_files() -> None:
    root = Path(__file__).resolve().parent.parent / "docs"
    page = (root / "index.html").read_text(encoding="utf-8")
    assert "claude/quirky-pascal-y84evb" in page and "location.replace" in page and "docs/site.json" in page
    assert (root / ".nojekyll").exists()
    assert json.loads((root / "site.json").read_text(encoding="utf-8")).keys() >= {"url"}


@pytest.mark.anyio
async def test_stale_address_from_previous_launch_is_never_published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Le journal du lancement précédent est lu avant que le nouveau tunnel existe : son adresse
    (tunnel fermé) ne doit pas être publiée, même longtemps après."""
    log = tmp_path / "tunnel.log"
    log.write_text(LOG, encoding="utf-8")
    import os
    old = portal.STARTED_AT.timestamp() - 3600
    os.utime(log, (old, old))  # écrit une heure avant le démarrage du serveur
    monkeypatch.setattr(portal, "public_url", lambda s: tunnel.public_url(s, log))
    now = {"t": 0.0}
    gh = FakeGitHub(existing_url=None)
    async with gh.client() as client:
        await portal.sync_portal_once(settings(), client, probe=silent, clock=lambda: now["t"])
        now["t"] = 10_000.0
        await portal.sync_portal_once(settings(), client, probe=silent, clock=lambda: now["t"])
    assert gh.puts == [] and portal.portal_state.published_url is None
    assert "lancement précédent" in portal.portal_state.waiting

    # Même vieille adresse, mais le tunnel répond (fenêtre restée ouverte) : publiée
    async with gh.client() as client:
        await portal.sync_portal_once(settings(), client, probe=answers, clock=lambda: now["t"])
    assert len(gh.puts) == 1 and portal.portal_state.waiting is None


@pytest.mark.anyio
async def test_fresh_address_waits_for_the_tunnel_then_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = tmp_path / "tunnel.log"
    log.write_text(LOG, encoding="utf-8")  # écrit maintenant : après le démarrage du serveur
    monkeypatch.setattr(portal, "public_url", lambda s: tunnel.public_url(s, log))
    now = {"t": 100.0}
    gh = FakeGitHub(existing_url=None)
    async with gh.client() as client:
        await portal.sync_portal_once(settings(), client, probe=silent, clock=lambda: now["t"])
        assert gh.puts == [] and "ne répond pas encore" in portal.portal_state.waiting
        now["t"] += portal.UNVERIFIED_PUBLISH_AFTER_S
        await portal.sync_portal_once(settings(), client, probe=silent, clock=lambda: now["t"])
    assert len(gh.puts) == 1  # publiée quand même : ce PC n'arrive peut-être pas à joindre son propre tunnel


@pytest.mark.anyio
async def test_base_url_domain_is_published_without_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(portal, "public_url", lambda s: tunnel.public_url(s, tmp_path / "absent.log"))
    gh = FakeGitHub(existing_url=None)
    async with gh.client() as client:
        await portal.sync_portal_once(settings(base_url="https://pekin.exemple.fr"), client, probe=silent)
    assert len(gh.puts) == 1
