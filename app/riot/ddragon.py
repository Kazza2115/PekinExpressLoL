"""Data Dragon : version du jeu, noms et icônes de champions / profils.

Jamais bloquant : chaque fonction réseau renvoie une valeur de repli en cas
d'échec (pas de réseau, timeout, JSON inattendu) et n'élève jamais d'exception.
Caches mémoire : version (1 h), `champion.json` par version. Un échec est lui
aussi mémorisé quelques minutes pour ne pas payer le timeout à chaque poll.
"""

from __future__ import annotations

import logging
import re
import time

import httpx

log = logging.getLogger("pekin.ddragon")

DDRAGON_BASE = "https://ddragon.leagueoflegends.com"
FALLBACK_VERSION = "14.24.1"
VERSION_TTL_S = 3600.0  # cache de la version
FAILURE_TTL_S = 600.0  # délai avant de retenter après un échec réseau
HTTP_TIMEOUT = httpx.Timeout(5.0)

# Dernière version connue (synchrone, pour construire des URLs sans attendre le réseau)
CURRENT_VERSION: str = FALLBACK_VERSION

_version_cache: tuple[str, float, bool] | None = None  # (version, horodatage monotonic, réseau OK)
_champions_cache: dict[str, dict[int, str]] = {}  # version → {championId: nom image}
_champions_failed_at: dict[str, float] = {}  # version → horodatage du dernier échec

# Noms affichés dont l'identifiant image Data Dragon ne suit pas la règle générique
_SPECIAL_IMAGE_NAMES: dict[str, str] = {
    "wukong": "MonkeyKing",
    "kai'sa": "Kaisa",
    "kha'zix": "Khazix",
    "cho'gath": "Chogath",
    "vel'koz": "Velkoz",
    "rek'sai": "RekSai",
    "kog'maw": "KogMaw",
    "bel'veth": "Belveth",
    "k'sante": "KSante",
    "leblanc": "Leblanc",
    "nunu & willump": "Nunu",
    "nunu": "Nunu",
    "renata glasc": "Renata",
    "dr. mundo": "DrMundo",
    "jarvan iv": "JarvanIV",
    "lee sin": "LeeSin",
    "master yi": "MasterYi",
    "miss fortune": "MissFortune",
    "tahm kench": "TahmKench",
    "twisted fate": "TwistedFate",
    "xin zhao": "XinZhao",
    "aurelion sol": "AurelionSol",
    "fiddlesticks": "Fiddlesticks",
}
_STRIP_RE = re.compile(r"[\s'’.&]")


def champion_image_name(champion_name: str) -> str:
    """Nom affiché → identifiant image Data Dragon ("Wukong" → "MonkeyKing").

    Idempotent sur un identifiant déjà normalisé (`championName` de Match-V5).
    """
    name = (champion_name or "").strip()
    if not name:
        return ""
    special = _SPECIAL_IMAGE_NAMES.get(name.lower())
    if special is not None:
        return special
    return _STRIP_RE.sub("", name)


def profile_icon_url(version: str, icon_id: int | None) -> str | None:
    if icon_id is None:
        return None
    return f"{DDRAGON_BASE}/cdn/{version or CURRENT_VERSION}/img/profileicon/{int(icon_id)}.png"


def champion_icon_url(version: str, champion_name: str | None) -> str | None:
    image = champion_image_name(champion_name or "")
    if not image:
        return None
    return f"{DDRAGON_BASE}/cdn/{version or CURRENT_VERSION}/img/champion/{image}.png"


async def get_version() -> str:
    """Dernière version Data Dragon (cache 1 h) ; `CURRENT_VERSION` en repli si réseau KO."""
    global CURRENT_VERSION, _version_cache
    now = time.monotonic()
    if _version_cache is not None:
        version, fetched_at, fetched_ok = _version_cache
        if now - fetched_at < (VERSION_TTL_S if fetched_ok else FAILURE_TTL_S):
            return version
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(f"{DDRAGON_BASE}/api/versions.json")
            response.raise_for_status()
            versions = response.json()
        version = str(versions[0]).strip()
        if not version:
            raise ValueError("liste de versions vide")
        CURRENT_VERSION = version
        _version_cache = (version, now, True)
    except Exception as exc:  # noqa: BLE001 — jamais bloquant
        log.warning("Data Dragon indisponible (%s) : version %s conservée", exc, CURRENT_VERSION)
        _version_cache = (CURRENT_VERSION, now, False)
    return CURRENT_VERSION


async def _load_champions(version: str) -> dict[int, str] | None:
    """Table championId → identifiant image pour une version (cache) ; None si KO."""
    table = _champions_cache.get(version)
    if table is not None:
        return table
    failed_at = _champions_failed_at.get(version)
    if failed_at is not None and time.monotonic() - failed_at < FAILURE_TTL_S:
        return None
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(f"{DDRAGON_BASE}/cdn/{version}/data/en_US/champion.json")
            response.raise_for_status()
            data = response.json()["data"]
        table = {int(entry["key"]): str(entry["id"]) for entry in data.values()}
    except Exception as exc:  # noqa: BLE001
        log.warning("champion.json (%s) indisponible : %s", version, exc)
        _champions_failed_at[version] = time.monotonic()
        return None
    _champions_cache[version] = table
    return table


async def champion_name_from_id(champion_id: int) -> str | None:
    """Identifiant image du champion (ex. 62 → "MonkeyKing") ; None si inconnu ou réseau KO."""
    try:
        table = await _load_champions(await get_version())
    except Exception:  # noqa: BLE001
        return None
    if table is None:
        return None
    return table.get(int(champion_id))


def reset_cache() -> None:
    """Vide les caches (tests)."""
    global CURRENT_VERSION, _version_cache
    CURRENT_VERSION = FALLBACK_VERSION
    _version_cache = None
    _champions_cache.clear()
    _champions_failed_at.clear()
