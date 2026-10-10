"""GIF des messages Discord : recherche Klipy dans une catégorie tirée au hasard.

Victoire → une catégorie de `DISCORD_GIF_SEARCH_WIN`, défaite → `DISCORD_GIF_SEARCH_LOSS`, puis
un GIF au hasard parmi les résultats Klipy (API gratuite, clé `KLIPY_API_KEY`). Sans clé, ou si
Klipy ne répond pas, un lien de secours (`DISCORD_GIF_WIN` / `DISCORD_GIF_LOSS`) est utilisé.
Jamais d'exception : au pire, pas de GIF.
"""

from __future__ import annotations

import logging
import random
import re
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
    _resolved.clear()
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


# --------------------------------------------------------------------------- #
# GIF choisi par l'organisateur (annonce du début) : page Klipy → lien direct du GIF
# --------------------------------------------------------------------------- #

KLIPY_ITEMS_URL = "https://api.klipy.com/api/v1/{key}/gifs/items"
_KLIPY_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?klipy\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:gifs|stickers|clips|memes)/([A-Za-z0-9_-]+)/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_META_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_META_KEY_RE = re.compile(r"""(?:property|name)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_META_CONTENT_RE = re.compile(r"""content\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_IMAGE_META_KEYS = ("og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src")
_resolved: dict[str, str] = {}


def klipy_slug(url: str) -> str | None:
    """« https://klipy.com/gifs/sponge-bob-bob-esponja » → « sponge-bob-bob-esponja » ; None sinon."""
    match = _KLIPY_PAGE_RE.match((url or "").strip())
    return match.group(1) if match else None


def image_from_html(html: str) -> str | None:
    """Image annoncée par une page (balises og:image / twitter:image), GIF animé de préférence."""
    found: list[str] = []
    for tag in _META_RE.findall(html or ""):
        key = _META_KEY_RE.search(tag)
        content = _META_CONTENT_RE.search(tag)
        if key and content and key.group(1).lower() in _IMAGE_META_KEYS:
            value = content.group(1).replace("&amp;", "&").strip()
            if value.startswith("https://"):
                found.append(value)
    for ext in (".gif", ".webp"):
        animated = next((u for u in found if u.lower().split("?", 1)[0].endswith(ext)), None)
        if animated:
            return animated
    return found[0] if found else None


async def _get(url: str, client: httpx.AsyncClient | None, **kwargs: Any) -> httpx.Response | None:
    try:
        if client is not None:
            return await client.get(url, timeout=KLIPY_TIMEOUT_S, follow_redirects=True, **kwargs)
        async with httpx.AsyncClient(timeout=KLIPY_TIMEOUT_S, follow_redirects=True) as own_client:
            return await own_client.get(url, **kwargs)
    except Exception as exc:  # noqa: BLE001 — jamais bloquant ; pas d'URL dans le log (clé possible)
        log.warning("GIF : %s injoignable (%s)", url.split("/api/v1/")[0], type(exc).__name__)
        return None


async def resolve_gif(
    url: str | None, *, settings: Settings | None = None, client: httpx.AsyncClient | None = None
) -> str | None:
    """Lien direct d'un GIF donné par l'organisateur : page Klipy (via l'API si KLIPY_API_KEY, sinon
    l'image annoncée par la page), page GIPHY, ou lien d'image tel quel. None si introuvable."""
    settings = settings if settings is not None else get_settings()
    raw = (url or "").strip()
    if not raw:
        return None
    if raw in _resolved:
        return _resolved[raw]
    from app.config import normalize_gif_url  # import local : évite d'alourdir l'en-tête

    normalized = normalize_gif_url(raw)
    if normalized is None:
        return None
    slug = klipy_slug(normalized)
    if slug is None:
        return normalized  # lien direct (ou page GIPHY déjà convertie)
    result: str | None = None
    key = (settings.klipy_api_key or "").strip()
    if key:
        response = await _get(
            KLIPY_ITEMS_URL.format(key=quote(key, safe="")), client, params={"slugs": slug, "customer_id": KLIPY_CUSTOMER_ID}
        )
        if response is not None and response.status_code < 400:
            try:
                urls = extract_gif_urls(response.json())
            except ValueError:
                urls = []
            result = urls[0] if urls else None
    if result is None:
        response = await _get(normalized, client, headers={"User-Agent": "Mozilla/5.0 (PekinExpressLoL)"})
        if response is not None and response.status_code < 400:
            result = image_from_html(response.text)
    if result:
        _resolved[raw] = result
    else:
        log.info("GIF %s : lien direct introuvable (la page sera mise en lien)", raw)
    return result
