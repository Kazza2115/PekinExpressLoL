"""`RiotClient` sur un `httpx.MockTransport` : erreurs, retries, en-tête, limiteur de débit.

Les attentes (`sleep`) et l'horloge du limiteur sont simulées : aucun test n'attend.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable

import httpx
import pytest

from app.config import get_settings, reload_settings
from app.riot.base import RiotError, RiotNotFound, RiotRateLimited, RiotUnauthorized
from app.riot.client import RiotClient


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FakeClock:
    """Horloge simulée : `sleep` avance le temps au lieu d'attendre."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 3))
        self.now += seconds


def make_client(handler: Callable[[httpx.Request], httpx.Response], **kwargs) -> tuple[RiotClient, FakeClock]:
    clock = FakeClock()
    settings = dataclasses.replace(get_settings(), riot_api_key="RGAPI-test-key")
    kwargs.setdefault("settings", settings)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = RiotClient(client=http, sleep=clock.sleep, clock=clock, **kwargs)
    return client, clock


def json_response(status: int, payload=None, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload if payload is not None else {}), headers=headers)


ACCOUNT = {"puuid": "puuid-1", "gameName": "La Peace", "tagLine": "CHILL"}


@pytest.mark.anyio
async def test_404_raises_not_found():
    client, _ = make_client(lambda request: json_response(404, {"status": {"message": "not found"}}))
    with pytest.raises(RiotNotFound) as excinfo:
        await client.get_account_by_riot_id("Inconnu", "EUW")
    assert excinfo.value.status == 404
    assert client.request_count == 1


@pytest.mark.anyio
async def test_401_and_403_raise_unauthorized():
    for status in (401, 403):
        client, _ = make_client(lambda request, status=status: json_response(status))
        with pytest.raises(RiotUnauthorized):
            await client.get_summoner_by_puuid("puuid-1")


@pytest.mark.anyio
async def test_429_then_200_is_retried():
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return json_response(429, headers={"Retry-After": "3"})
        return json_response(200, ACCOUNT)

    client, clock = make_client(handler)
    account = await client.get_account_by_riot_id("La Peace", "CHILL")
    assert account.puuid == "puuid-1"
    assert account.game_name == "La Peace" and account.tag_line == "CHILL"
    assert len(calls) == 2
    assert client.request_count == 2
    assert clock.sleeps == [3.0]  # Retry-After respecté


@pytest.mark.anyio
async def test_429_without_header_uses_default_delay_then_gives_up():
    client, clock = make_client(lambda request: json_response(429))
    with pytest.raises(RiotRateLimited):
        await client.get_match("EUW1_1")
    assert clock.sleeps == [2.0, 2.0, 2.0]  # 3 nouvelles tentatives au plus
    assert client.request_count == 4


@pytest.mark.anyio
async def test_5xx_exponential_backoff_then_success():
    statuses = iter([500, 503])

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses, 200)
        return json_response(status, ["EUW1_1", "EUW1_2"] if status == 200 else {})

    client, clock = make_client(handler)
    ids = await client.get_match_ids_by_puuid("puuid-1", 420, start_time=1_700_000_000, count=5)
    assert ids == ["EUW1_1", "EUW1_2"]
    assert clock.sleeps == [1.0, 2.0]


@pytest.mark.anyio
async def test_5xx_exhausted_raises_riot_error_with_status():
    client, clock = make_client(lambda request: json_response(502))
    with pytest.raises(RiotError) as excinfo:
        await client.get_match("EUW1_1")
    assert excinfo.value.status == 502
    assert clock.sleeps == [1.0, 2.0, 4.0]


@pytest.mark.anyio
async def test_network_error_is_retried_like_5xx():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectTimeout("timeout", request=request)
        return json_response(200, ACCOUNT)

    client, clock = make_client(handler)
    account = await client.get_account_by_riot_id("La Peace", "CHILL")
    assert account.puuid == "puuid-1"
    assert clock.sleeps == [1.0]


@pytest.mark.anyio
async def test_other_status_raises_riot_error():
    client, _ = make_client(lambda request: json_response(418))
    with pytest.raises(RiotError) as excinfo:
        await client.get_match("EUW1_1")
    assert excinfo.value.status == 418


@pytest.mark.anyio
async def test_riot_token_header_and_url_encoding():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(200, ACCOUNT)

    client, _ = make_client(handler)
    await client.get_account_by_riot_id("La Peace", "CHILL")
    request = seen[0]
    assert request.headers["X-Riot-Token"] == "RGAPI-test-key"
    assert request.url.host == "europe.api.riotgames.com"
    assert request.url.raw_path == b"/riot/account/v1/accounts/by-riot-id/La%20Peace/CHILL"  # espace encodé


@pytest.mark.anyio
async def test_token_is_read_from_settings_on_every_request(monkeypatch):
    """Sans settings injectées, la clé vient de `get_settings()` → rechargeable à chaud."""
    tokens: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tokens.append(request.headers.get("X-Riot-Token", ""))
        return json_response(200, ACCOUNT)

    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-first")
    monkeypatch.setenv("DEMO_MODE", "0")
    reload_settings()
    client, _ = make_client(handler, settings=None)
    await client.get_account_by_riot_id("La Peace", "CHILL")
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-second")
    reload_settings()
    await client.get_account_by_riot_id("La Peace", "CHILL")
    assert tokens == ["RGAPI-first", "RGAPI-second"]


@pytest.mark.anyio
async def test_active_game_404_returns_none():
    client, _ = make_client(lambda request: json_response(404))
    assert await client.get_active_game("puuid-1") is None


@pytest.mark.anyio
async def test_active_game_parses_participant_champion():
    payload = {
        "gameId": 123,
        "gameStartTime": 1_700_000_000_000,
        "gameQueueConfigId": 420,
        "gameMode": "CLASSIC",
        "participants": [
            {"puuid": "other", "championId": 1},
            {"puuid": "puuid-1", "championId": 62},
        ],
    }
    client, _ = make_client(lambda request: json_response(200, payload))
    game = await client.get_active_game("puuid-1")
    assert game is not None
    assert (game.game_id, game.queue_id, game.game_mode, game.champion_id) == (123, 420, "CLASSIC", 62)
    assert game.game_start.isoformat() == "2023-11-14T22:13:20+00:00"
    assert game.champion_name is None


@pytest.mark.anyio
async def test_league_entries_404_is_an_error_and_parses_entries():
    # Un joueur non classé = `200 []` ; un 404 est une vraie erreur (puuid / plateforme)
    client, _ = make_client(lambda request: json_response(404))
    with pytest.raises(RiotNotFound):
        await client.get_league_entries_by_puuid("puuid-1")
    client, _ = make_client(lambda request: json_response(200, []))
    assert await client.get_league_entries_by_puuid("puuid-1") == []

    entries_payload = [
        {"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "II", "leaguePoints": 45,
         "wins": 10, "losses": 8, "hotStreak": True},
        {"queueType": "CHERRY"},  # file sans tier : ignorée
    ]
    client, _ = make_client(lambda request: json_response(200, entries_payload))
    entries = await client.get_league_entries_by_puuid("puuid-1")
    assert len(entries) == 1
    entry = entries[0]
    assert (entry.queue_type, entry.tier, entry.rank, entry.league_points) == ("RANKED_SOLO_5x5", "GOLD", "II", 45)
    assert (entry.wins, entry.losses, entry.hot_streak) == (10, 8, True)


@pytest.mark.anyio
async def test_rate_limiter_allows_burst_of_20_then_waits():
    client, clock = make_client(lambda request: json_response(200, ACCOUNT))
    for _ in range(20):
        await client.get_account_by_riot_id("La Peace", "CHILL")
    assert clock.sleeps == []  # 20 requêtes immédiates par seconde
    await client.get_account_by_riot_id("La Peace", "CHILL")
    assert len(clock.sleeps) == 1 and 0 < clock.sleeps[0] <= 1.0
    assert client.request_count == 21


@pytest.mark.anyio
async def test_rate_limiter_is_per_host():
    client, clock = make_client(lambda request: json_response(200, {"puuid": "p", "profileIconId": 1, "summonerLevel": 2}))
    for _ in range(20):
        await client.get_account_by_riot_id("La Peace", "CHILL")  # host régional
    await client.get_summoner_by_puuid("puuid-1")  # host plateforme : fenêtre séparée
    assert clock.sleeps == []


@pytest.mark.anyio
async def test_rate_limiter_long_window(monkeypatch):
    client, clock = make_client(lambda request: json_response(200, ACCOUNT), rate_limits=[(3, 1.0), (5, 10.0)])
    for _ in range(5):
        await client.get_account_by_riot_id("La Peace", "CHILL")
    # 3 immédiates, puis attente de la fenêtre courte pour les 2 suivantes
    assert len(clock.sleeps) == 1 and clock.sleeps[0] == pytest.approx(1.0)
    await client.get_account_by_riot_id("La Peace", "CHILL")  # 6e : fenêtre longue (5 / 10 s) pleine
    assert clock.sleeps[-1] == pytest.approx(9.0)


@pytest.mark.anyio
async def test_aclose_closes_only_owned_client():
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: json_response(200)))
    client = RiotClient(client=http)
    await client.aclose()
    assert not http.is_closed
    await http.aclose()
