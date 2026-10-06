"""Client HTTP réel de l'API Riot (implémente `RiotAPI`).

- la clé `X-Riot-Token` est relue dans `get_settings()` à **chaque** requête, ce qui
  permet de changer de clé via `reload_settings()` sans redémarrer ;
- limiteur de débit par host (token bucket 20 req / 1 s et 100 req / 2 min, limites
  d'une clé de développement) : verrou asyncio + horodatages, sans boucle active ;
- retries : 429 → attente `Retry-After` (2 s par défaut), 5xx et erreurs réseau →
  backoff exponentiel 1 / 2 / 4 s ; 3 nouvelles tentatives au maximum ;
- 404 → `RiotNotFound`, 401/403 → `RiotUnauthorized`, autres → `RiotError(status)`.

`sleep` / `clock` / `rate_limits` sont injectables (tests sans attente réelle).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import Settings, get_settings
from app.riot import endpoints
from app.riot.base import (
    AccountDTO,
    ActiveGameDTO,
    LeagueEntryDTO,
    RiotError,
    RiotNotFound,
    RiotRateLimited,
    RiotUnauthorized,
    SummonerDTO,
)

log = logging.getLogger("pekin.riot")

# (nombre de requêtes, fenêtre en secondes) — limites d'une clé de développement
DEFAULT_RATE_LIMITS: list[tuple[int, float]] = [(20, 1.0), (100, 120.0)]
DEFAULT_RETRY_AFTER_S = 2.0
MAX_RETRIES = 3
BACKOFF_BASE_S = 1.0
REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

SleepFn = Callable[[float], Awaitable[None]]
ClockFn = Callable[[], float]


class _RateLimiter:
    """Token bucket multi-fenêtres pour un host : bloque (sans boucle active) tant qu'une
    fenêtre est pleine ou qu'un `Retry-After` est en cours."""

    def __init__(self, limits: list[tuple[int, float]], clock: ClockFn, sleep: SleepFn):
        self._limits = limits
        self._clock = clock
        self._sleep = sleep
        self._stamps: list[deque[float]] = [deque() for _ in limits]
        self._lock = asyncio.Lock()
        self._blocked_until = 0.0

    def block_for(self, seconds: float) -> None:
        """Gèle le host pendant `seconds` (Retry-After reçu)."""
        self._blocked_until = max(self._blocked_until, self._clock() + max(0.0, seconds))

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                wait = max(0.0, self._blocked_until - now)
                for (limit, window), stamps in zip(self._limits, self._stamps):
                    while stamps and stamps[0] <= now - window:
                        stamps.popleft()
                    if len(stamps) >= limit:
                        wait = max(wait, stamps[0] + window - now)
                if wait <= 0:
                    for stamps in self._stamps:
                        stamps.append(now)
                    return
                await self._sleep(wait)


def _retry_after_seconds(response: httpx.Response) -> float:
    """`Retry-After` en secondes (format entier Riot) ; valeur par défaut sinon."""
    raw = response.headers.get("Retry-After")
    try:
        return max(0.0, float(raw)) if raw is not None else DEFAULT_RETRY_AFTER_S
    except ValueError:
        return DEFAULT_RETRY_AFTER_S


class RiotClient:
    """Client asynchrone de l'API Riot (voir docstring du module)."""

    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
        *,
        rate_limits: list[tuple[int, float]] | None = None,
        sleep: SleepFn | None = None,
        clock: ClockFn | None = None,
        max_retries: int = MAX_RETRIES,
    ):
        # Sans `settings` injectées, on relit `get_settings()` à chaque requête (clé rechargeable)
        self._settings_override = settings
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT)
        self._owns_client = client is None
        self._rate_limits = list(rate_limits or DEFAULT_RATE_LIMITS)
        self._sleep: SleepFn = sleep or asyncio.sleep
        self._clock: ClockFn = clock or time.monotonic
        self._max_retries = max(0, max_retries)
        self._limiters: dict[str, _RateLimiter] = {}
        self.request_count = 0

    # ------------------------------------------------------------------ interne

    def _settings(self) -> Settings:
        return self._settings_override or get_settings()

    def _limiter_for(self, url: str) -> _RateLimiter:
        host = urlparse(url).netloc
        limiter = self._limiters.get(host)
        if limiter is None:
            limiter = _RateLimiter(self._rate_limits, self._clock, self._sleep)
            self._limiters[host] = limiter
        return limiter

    async def _get(self, url: str) -> Any:
        """GET avec limiteur de débit et retries ; renvoie le JSON décodé."""
        limiter = self._limiter_for(url)
        path = urlparse(url).path
        retries_429 = 0
        retries_5xx = 0
        while True:
            await limiter.acquire()
            headers = {"X-Riot-Token": self._settings().riot_api_key}
            self.request_count += 1
            try:
                response = await self._client.get(url, headers=headers)
            except httpx.HTTPError as exc:
                # Erreur réseau / timeout : même traitement qu'un 5xx
                retries_5xx += 1
                if retries_5xx > self._max_retries:
                    raise RiotError(f"Erreur réseau Riot sur {path} : {exc}") from exc
                delay = BACKOFF_BASE_S * 2 ** (retries_5xx - 1)
                log.warning("Riot : erreur réseau sur %s (%s), nouvel essai dans %.0fs (%d/%d)",
                            path, type(exc).__name__, delay, retries_5xx, self._max_retries)
                await self._sleep(delay)
                continue

            status = response.status_code
            if 200 <= status < 300:
                return response.json() if response.content else None
            if status == 404:
                raise RiotNotFound(f"Riot 404 sur {path}", status=404)
            if status in (401, 403):
                raise RiotUnauthorized(f"Riot {status} sur {path} : clé invalide ou expirée", status=status)
            if status == 429:
                retries_429 += 1
                if retries_429 > self._max_retries:
                    raise RiotRateLimited(f"Riot 429 persistant sur {path}", status=429)
                delay = _retry_after_seconds(response)
                limiter.block_for(delay)
                log.warning("Riot : 429 sur %s, nouvel essai dans %.0fs (%d/%d)",
                            path, delay, retries_429, self._max_retries)
                await self._sleep(delay)
                continue
            if status >= 500:
                retries_5xx += 1
                if retries_5xx > self._max_retries:
                    raise RiotError(f"Riot {status} sur {path}", status=status)
                delay = BACKOFF_BASE_S * 2 ** (retries_5xx - 1)
                log.warning("Riot : %d sur %s, nouvel essai dans %.0fs (%d/%d)",
                            status, path, delay, retries_5xx, self._max_retries)
                await self._sleep(delay)
                continue
            raise RiotError(f"Riot {status} sur {path}", status=status)

    # ------------------------------------------------------------------ RiotAPI

    async def get_account_by_riot_id(self, game_name: str, tag_line: str) -> AccountDTO:
        url = endpoints.account_url(self._settings().region_host, game_name, tag_line)
        return endpoints.parse_account(await self._get(url))

    async def get_summoner_by_puuid(self, puuid: str) -> SummonerDTO:
        url = endpoints.summoner_url(self._settings().platform_host, puuid)
        return endpoints.parse_summoner(await self._get(url))

    async def get_league_entries_by_puuid(self, puuid: str) -> list[LeagueEntryDTO]:
        url = endpoints.league_entries_url(self._settings().platform_host, puuid)
        try:
            data = await self._get(url)
        except RiotNotFound:
            return []  # joueur inconnu de League-V4 = unranked
        return endpoints.parse_league_entries(data or [])

    async def get_match_ids_by_puuid(
        self,
        puuid: str,
        queue_id: int,
        start_time: int | None = None,
        count: int = 20,
    ) -> list[str]:
        url = endpoints.match_ids_url(self._settings().region_host, puuid, queue_id, start_time, count)
        return endpoints.parse_match_ids(await self._get(url))

    async def get_match(self, match_id: str) -> dict[str, Any]:
        url = endpoints.match_url(self._settings().region_host, match_id)
        data = await self._get(url)
        if not isinstance(data, dict):
            raise RiotError(f"Riot : réponse Match-V5 inattendue pour {match_id}")
        return data

    async def get_active_game(self, puuid: str) -> ActiveGameDTO | None:
        url = endpoints.active_game_url(self._settings().platform_host, puuid)
        try:
            data = await self._get(url)
        except RiotNotFound:
            return None  # pas en partie
        return endpoints.parse_active_game(data or {}, puuid)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
