"""GIF des messages Discord : recherche Klipy dans une catégorie tirée au hasard.

Victoire → une catégorie de `DISCORD_GIF_SEARCH_WIN`, défaite → `DISCORD_GIF_SEARCH_LOSS`, puis
un GIF au hasard parmi les résultats Klipy (API gratuite, clé `KLIPY_API_KEY`). Sans clé, ou si
Klipy ne répond pas, un lien de secours (`DISCORD_GIF_WIN` / `DISCORD_GIF_LOSS`) est utilisé.
Jamais d'exception : au pire, pas de GIF.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.config import Settings, get_settings

log = logging.getLogger(__name__)

KLIPY_SEARCH_URL = "https://api.klipy.com/api/v1/{key}/gifs/search"
KLIPY_TIMEOUT_S = 5.0
KLIPY_PER_PAGE = 24
# Filtre de contenu Klipy (off, low, medium, high) : « medium » écarte le contenu choquant
KLIPY_CONTENT_FILTER = "medium"
KLIPY_CUSTOMER_ID = "pekin-express-lol"
# Résultats d'une catégorie gardés 1 h (une recherche par catégorie et par heure au plus)
CACHE_TTL_S = 3600.0
# Taille préférée du GIF (Klipy : hd, md, sm, xs) : « md » reste léger dans Discord
RENDITION_ORDER = ("md", "hd", "sm", "xs")
# Catégories essayées au plus par message (si une recherche ne renvoie rien)
MAX_TERMS_TRIED = 3
# Après une erreur (réseau, clé refusée), Klipy n'est plus interrogé pendant 5 min : un Klipy en
# panne ne doit pas ralentir le suivi des parties
FAILURE_BACKOFF_S = 300.0

_cache: dict[str, tuple[float, tuple[str, ...]]] = {}
_failed_at: float | None = None


@dataclass(frozen=True)
class GifChoice:
    url: str
    query: str | None = None  # catégorie Klipy ; None pour un lien de secours


def reset_cache() -> None:
    """Vide le cache des recherches (tests, rechargement de `.env`)."""
    global _failed_at
    _cache.clear()
    _failed_at = None


def extract_gif_urls(payload: Any) -> list[str]:
    """Liens des GIF d'une réponse Klipy `{"result": true, "data": {"data": [...]}}` (publicités ignorées)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    items = data.get("data") if isinstance(data, dict) else None
    urls: list[str] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("type") == "ad":
            continue
        files = item.get("file") if isinstance(item.get("file"), dict) else {}
        for size in RENDITION_ORDER:
            rendition = files.get(size) if isinstance(files.get(size), dict) else {}
            gif = rendition.get("gif") if isinstance(rendition.get("gif"), dict) else {}
            url = gif.get("url")
            if isinstance(url, str) and url.startswith("https://"):
                urls.append(url)
                break
    return urls


async def search_klipy(
    query: str, *, settings: Settings | None = None, client: httpx.AsyncClient | None = None
) -> tuple[str, ...] | None:
    """GIF trouvés par Klipy pour `query` (cache 1 h) : vide si rien trouvé, None si pas de clé ou erreur."""
    global _failed_at
    settings = settings if settings is not None else get_settings()
    key = (settings.klipy_api_key or "").strip()
    if not key or not query.strip():
        return None
    cache_key = query.strip().lower()
    cached = _cache.get(cache_key)
    if cached is not None and time.monotonic() - cached[0] < CACHE_TTL_S:
        return cached[1]
    if _failed_at is not None and time.monotonic() - _failed_at < FAILURE_BACKOFF_S:
        return None
    url = KLIPY_SEARCH_URL.format(key=quote(key, safe=""))
    params = {
        "q": query.strip(),
        "page": 1,
        "per_page": KLIPY_PER_PAGE,
        "customer_id": KLIPY_CUSTOMER_ID,
        "content_filter": KLIPY_CONTENT_FILTER,
        "format_filter": "gif",
    }
    try:
        if client is not None:
            response = await client.get(url, params=params, timeout=KLIPY_TIMEOUT_S)
        else:
            async with httpx.AsyncClient(timeout=KLIPY_TIMEOUT_S) as own_client:
                response = await own_client.get(url, params=params)
    except Exception as exc:  # noqa: BLE001 — jamais bloquant ; pas d'URL dans le log (elle contient la clé)
        log.warning("Klipy injoignable (%s) : pas de GIF « %s »", type(exc).__name__, query)
        _failed_at = time.monotonic()
        return None
    if response.status_code >= 400:
        hint = " — clé KLIPY_API_KEY refusée ?" if response.status_code in (401, 403) else ""
        log.warning("Klipy : HTTP %s pour « %s »%s", response.status_code, query, hint)
        _failed_at = time.monotonic()
        return None
    try:
        urls = tuple(extract_gif_urls(response.json()))
    except ValueError:
        log.warning("Klipy : réponse illisible pour « %s »", query)
        _failed_at = time.monotonic()
        return None
    _failed_at = None
    if urls:
        _cache[cache_key] = (time.monotonic(), urls)
    else:
        log.info("Klipy : aucun GIF pour « %s »", query)
    return urls


async def find_gif(
    win: bool,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
    rng: random.Random | None = None,
) -> GifChoice | None:
    """GIF au hasard pour une victoire (`win`) ou une défaite ; None si aucun n'est disponible."""
    settings = settings if settings is not None else get_settings()
    rng = rng or random.Random()
    terms = list(settings.gif_search_win if win else settings.gif_search_loss)
    if settings.klipy_api_key and terms:
        rng.shuffle(terms)
        for term in terms[:MAX_TERMS_TRIED]:
            urls = await search_klipy(term, settings=settings, client=client)
            if urls is None:
                break  # Klipy indisponible : lien de secours
            if urls:
                return GifChoice(url=rng.choice(urls), query=term)
    fallback = settings.gif_fallback_win if win else settings.gif_fallback_loss
    if fallback:
        return GifChoice(url=rng.choice(list(fallback)))
    return None
