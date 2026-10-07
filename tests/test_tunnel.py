"""Adresse publique : BASE_URL, journal du tunnel cloudflared, repli local."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.services import tunnel


def make_settings(base_url: str) -> Settings:
    return Settings(
        riot_api_key="", riot_platform="euw1", riot_region="europe", demo_mode=True,
        poll_interval_seconds=10, track_flex=False, admin_password="x", database_url="sqlite://",
        games_per_day=10, max_players=8, timezone="Europe/Paris", discord_webhook_url="",
        base_url=base_url,
    )


LOG = """2026-10-07T09:07:40Z INF Thank you for trying Cloudflare Tunnel.
2026-10-07T09:07:41Z INF +--------------------------------------------------------------------------------------------+
2026-10-07T09:07:41Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
2026-10-07T09:07:41Z INF |  https://rec-explained-donation-frequency.trycloudflare.com                                |
2026-10-07T09:07:41Z INF +--------------------------------------------------------------------------------------------+
"""


def test_is_local_url():
    assert tunnel.is_local_url("http://localhost:8000")
    assert tunnel.is_local_url("http://127.0.0.1:8000")
    assert not tunnel.is_local_url("https://abc.trycloudflare.com")


def test_detect_tunnel_url(tmp_path: Path):
    log = tmp_path / "tunnel.log"
    assert tunnel.detect_tunnel_url(log) is None  # pas de journal
    log.write_text(LOG, encoding="utf-8")
    url, stamp = tunnel.detect_tunnel_url(log)
    assert url == "https://rec-explained-donation-frequency.trycloudflare.com"
    assert stamp.endswith("+00:00")
    log.write_text(LOG + "2026-10-07T10:00:00Z INF |  https://second-tunnel.trycloudflare.com  |\n", encoding="utf-8")
    assert tunnel.detect_tunnel_url(log)[0] == "https://second-tunnel.trycloudflare.com"


def test_public_url_priority(tmp_path: Path):
    log = tmp_path / "tunnel.log"
    log.write_text(LOG, encoding="utf-8")
    # BASE_URL publique : prioritaire sur le tunnel
    env = tunnel.public_url(make_settings("https://pekin.exemple.fr"), log)
    assert (env.url, env.source) == ("https://pekin.exemple.fr", "env")
    # BASE_URL locale : adresse du tunnel détectée
    tun = tunnel.public_url(make_settings("http://localhost:8000"), log)
    assert tun.source == "tunnel" and tun.url.endswith(".trycloudflare.com") and tun.detected_at
    # Ni l'un ni l'autre : adresse locale
    local = tunnel.public_url(make_settings("http://localhost:8000"), tmp_path / "absent.log")
    assert (local.url, local.source) == ("http://localhost:8000", "local")
    assert tunnel.effective_base_url(make_settings("https://pekin.exemple.fr")) == "https://pekin.exemple.fr"
