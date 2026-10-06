"""Tests du webhook Discord (transport httpx simulé) et des formateurs de messages."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from app.config import Settings
from app.db.models import Challenge, MatchParticipant, Player, Queue, Team
from app.services import notifications

pytestmark = pytest.mark.anyio

WEBHOOK_URL = "https://discord.test/api/webhooks/123/abc"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_settings(webhook_url: str) -> Settings:
    return Settings(
        riot_api_key="",
        riot_platform="euw1",
        riot_region="europe",
        demo_mode=True,
        poll_interval_seconds=10,
        track_flex=False,
        admin_password="x",
        database_url="sqlite://",
        games_per_day=10,
        max_players=8,
        timezone="Europe/Paris",
        discord_webhook_url=webhook_url,
        base_url="http://localhost:8000",
    )


def make_client(status_code: int, seen: list[httpx.Request]) -> httpx.AsyncClient:
    """Client httpx dont le transport enregistre les requêtes et répond `status_code`."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status_code, json={"ok": status_code < 400})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# send_discord
# --------------------------------------------------------------------------- #


async def test_send_discord_posts_content_and_returns_true_on_200():
    seen: list[httpx.Request] = []
    async with make_client(200, seen) as client:
        sent = await notifications.send_discord(
            "Salut **Mike**", settings=make_settings(WEBHOOK_URL), client=client
        )
    assert sent is True
    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == WEBHOOK_URL
    assert json.loads(request.content) == {"content": "Salut **Mike**", "allowed_mentions": {"parse": []}}
    assert request.headers["content-type"].startswith("application/json")


async def test_send_discord_returns_false_on_server_error():
    seen: list[httpx.Request] = []
    async with make_client(500, seen) as client:
        sent = await notifications.send_discord("x", settings=make_settings(WEBHOOK_URL), client=client)
    assert sent is False
    assert len(seen) == 1  # la requête est bien partie, c'est la réponse qui est en erreur


async def test_send_discord_without_webhook_is_a_noop():
    seen: list[httpx.Request] = []
    async with make_client(200, seen) as client:
        sent = await notifications.send_discord("x", settings=make_settings(""), client=client)
    assert sent is False
    assert seen == []


async def test_send_discord_swallows_transport_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("réseau indisponible", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        sent = await notifications.send_discord("x", settings=make_settings(WEBHOOK_URL), client=client)
    assert sent is False


async def test_send_discord_truncates_long_messages():
    seen: list[httpx.Request] = []
    async with make_client(204, seen) as client:
        sent = await notifications.send_discord("a" * 5000, settings=make_settings(WEBHOOK_URL), client=client)
    assert sent is True
    content = json.loads(seen[0].content)["content"]
    assert len(content) <= notifications.DISCORD_MAX_LENGTH
    assert content.endswith("…")


def test_truncate():
    assert notifications.truncate("court", 1900) == "court"
    assert notifications.truncate("x" * 10, 10) == "x" * 10
    assert notifications.truncate("abcdefghij", 5) == "abcd…"


# --------------------------------------------------------------------------- #
# Formateurs
# --------------------------------------------------------------------------- #


def mike() -> Player:
    return Player(id=1, display_name="Mike", game_name="La Peace", tag_line="CHILL", puuid="p-mike")


def duo_rouge() -> Team:
    return Team(id=1, name="Duo Rouge", color="#ef4444", slot=1)


def participant(*, win: bool, kills: int, deaths: int, assists: int, queue: Queue = Queue.SOLO) -> MatchParticipant:
    return MatchParticipant(
        match_id="EUW1_1",
        player_id=1,
        queue=queue,
        game_start=datetime.now(timezone.utc),
        game_duration=1800,
        champion_name="Ahri",
        win=win,
        kills=kills,
        deaths=deaths,
        assists=assists,
    )


def test_format_live_start():
    assert (
        notifications.format_live_start(mike(), duo_rouge(), "Ahri")
        == "🔴 **Mike** (Duo Rouge) vient de lancer une partie — **Ahri**"
    )
    # Sans duo (avant le tirage) : pas de parenthèse
    assert notifications.format_live_start(mike(), None, "Ahri") == "🔴 **Mike** vient de lancer une partie — **Ahri**"


def test_format_match_recorded_win_and_loss():
    win = participant(win=True, kills=7, deaths=2, assists=9)
    assert (
        notifications.format_match_recorded(mike(), duo_rouge(), win, 21)
        == "✅ **Mike** (Duo Rouge) gagne avec **Ahri** · 7/2/9 · +21 LP"
    )
    loss = participant(win=False, kills=2, deaths=8, assists=3)
    assert (
        notifications.format_match_recorded(mike(), duo_rouge(), loss, -17)
        == "❌ **Mike** (Duo Rouge) perd avec **Ahri** · 2/8/3 · −17 LP"
    )


def test_format_match_recorded_without_lp_or_team():
    win = participant(win=True, kills=7, deaths=2, assists=9)
    assert notifications.format_match_recorded(mike(), None, win, None) == "✅ **Mike** gagne avec **Ahri** · 7/2/9"
    flex = participant(win=True, kills=1, deaths=1, assists=1, queue=Queue.FLEX)
    assert notifications.format_match_recorded(mike(), None, flex, 0) == "✅ **Mike** gagne avec **Ahri** · 1/1/1 · ±0 LP · Flex"


def test_format_lp_delta():
    assert notifications.format_lp_delta(21) == "+21 LP"
    assert notifications.format_lp_delta(-17) == "−17 LP"
    assert notifications.format_lp_delta(0) == "±0 LP"


def test_format_challenge_started():
    start_at = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
    challenge = Challenge(name="Pékin Express LoL", games_per_day=10, start_at=start_at)
    text = notifications.format_challenge_started(challenge, settings=make_settings(""))
    assert text.startswith("🚀 **Pékin Express LoL** : le challenge commence !")
    assert "**10 parties par jour**" in text
    assert "Début : 10/10/2026 20:00" in text  # Europe/Paris = UTC+2 en octobre
    assert "http://localhost:8000/dashboard" in text


def test_format_draw_done():
    teams = [
        {"id": 2, "name": "Duo Bleu", "color": "#3b82f6", "slot": 2, "player_ids": [3, 4]},
        {"id": 1, "name": "Duo Rouge", "color": "#ef4444", "slot": 1, "player_ids": [1, 2]},
    ]
    players_by_id = {
        1: Player(id=1, display_name="Mike"),
        2: Player(id=2, display_name="Jean"),
        3: Player(id=3, display_name="Léa"),
        # 4 absent → libellé générique
    }
    text = notifications.format_draw_done(teams, players_by_id)
    lines = text.split("\n")
    assert lines[0] == "🎡 **Les duos sont tirés !**"
    # Ordre de tirage (slot), pas l'ordre de la liste
    assert lines[1] == "• **Duo Rouge** : Mike & Jean"
    assert lines[2] == "• **Duo Bleu** : Léa & Joueur 4"


def test_format_match_recorded_is_short_enough_for_discord():
    long_player = Player(id=1, display_name="M" * 60)
    text = notifications.format_match_recorded(
        long_player, duo_rouge(), participant(win=True, kills=0, deaths=0, assists=0), 15
    )
    assert len(text) < notifications.DISCORD_MAX_LENGTH
