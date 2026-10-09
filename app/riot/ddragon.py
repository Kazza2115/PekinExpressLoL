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


def champion_splash_url(champion_name: str | None) -> str | None:
    """Splash art (1215×717) du skin de base ; sans version (chemin stable)."""
    image = champion_image_name(champion_name or "")
    if not image:
        return None
    return f"{DDRAGON_BASE}/cdn/img/champion/splash/{image}_0.jpg"


def champion_loading_url(champion_name: str | None) -> str | None:
    """Illustration verticale « écran de chargement » (308×560) du skin de base."""
    image = champion_image_name(champion_name or "")
    if not image:
        return None
    return f"{DDRAGON_BASE}/cdn/img/champion/loading/{image}_0.jpg"


def item_icon_url(version: str, item_id: int | None) -> str | None:
    """Icône d'un objet ; None pour un emplacement vide (0 / None)."""
    if item_id is None:
        return None
    try:
        item = int(item_id)
    except (TypeError, ValueError):
        return None
    if item <= 0:
        return None
    return f"{DDRAGON_BASE}/cdn/{version or CURRENT_VERSION}/img/item/{item}.png"


# Sorts d'invocateur : `summoner1Id` / `summoner2Id` Match-V5 → nom d'image Data Dragon
SUMMONER_SPELLS: dict[int, str] = {
    1: "SummonerBoost",  # Purification
    3: "SummonerExhaust",  # Épuisement
    4: "SummonerFlash",  # Flash
    6: "SummonerHaste",  # Fantôme
    7: "SummonerHeal",  # Soin
    11: "SummonerSmite",  # Châtiment
    12: "SummonerTeleport",  # Téléportation
    13: "SummonerMana",  # Clarté
    14: "SummonerDot",  # Embrasement
    21: "SummonerBarrier",  # Barrière
    32: "SummonerSnowball",  # Boule de neige (ARAM)
}


def spell_icon_url(version: str, spell_id: int | None) -> str | None:
    """Icône d'un sort d'invocateur ; None si l'identifiant est inconnu."""
    if spell_id is None:
        return None
    try:
        name = SUMMONER_SPELLS.get(int(spell_id))
    except (TypeError, ValueError):
        return None
    if name is None:
        return None
    return f"{DDRAGON_BASE}/cdn/{version or CURRENT_VERSION}/img/spell/{name}.png"


# Runes : arbre (style) et rune principale (keystone) → image Data Dragon (chemins stables, sans version)
RUNE_STYLES: dict[int, tuple[str, str]] = {
    8000: ("Précision", "perk-images/Styles/7201_Precision.png"),
    8100: ("Domination", "perk-images/Styles/7200_Domination.png"),
    8200: ("Sorcellerie", "perk-images/Styles/7202_Sorcery.png"),
    8300: ("Inspiration", "perk-images/Styles/7203_Whimsy.png"),
    8400: ("Volonté", "perk-images/Styles/7204_Resolve.png"),
}
KEYSTONES: dict[int, tuple[str, str]] = {
    8005: ("Attaque soutenue", "perk-images/Styles/Precision/PressTheAttack/PressTheAttack.png"),
    8008: ("Tempo mortel", "perk-images/Styles/Precision/LethalTempo/LethalTempoTemp.png"),
    8021: ("Jeu de jambes", "perk-images/Styles/Precision/FleetFootwork/FleetFootwork.png"),
    8010: ("Conquérant", "perk-images/Styles/Precision/Conqueror/Conqueror.png"),
    8112: ("Électrocution", "perk-images/Styles/Domination/Electrocute/Electrocute.png"),
    8124: ("Prédateur", "perk-images/Styles/Domination/Predator/Predator.png"),
    8128: ("Moisson noire", "perk-images/Styles/Domination/DarkHarvest/DarkHarvest.png"),
    9923: ("Pluie de lames", "perk-images/Styles/Domination/HailOfBlades/HailOfBlades.png"),
    8214: ("Invocation d'Aery", "perk-images/Styles/Sorcery/SummonAery/SummonAery.png"),
    8229: ("Comète arcanique", "perk-images/Styles/Sorcery/ArcaneComet/ArcaneComet.png"),
    8230: ("Rush de phase", "perk-images/Styles/Sorcery/PhaseRush/PhaseRush.png"),
    8437: ("Poigne de l'immortel", "perk-images/Styles/Resolve/GraspOfTheUndying/GraspOfTheUndying.png"),
    8439: ("Répercussion", "perk-images/Styles/Resolve/VeteranAftershock/VeteranAftershock.png"),
    8465: ("Gardien", "perk-images/Styles/Resolve/Guardian/Guardian.png"),
    8351: ("Augmentation glaciale", "perk-images/Styles/Inspiration/GlacialAugment/GlacialAugment.png"),
    8360: ("Grimoire déchaîné", "perk-images/Styles/Inspiration/UnsealedSpellbook/UnsealedSpellbook.png"),
    8369: ("Premier coup", "perk-images/Styles/Inspiration/FirstStrike/FirstStrike.png"),
}


def rune_icon(rune_id: int | None, *, style: bool = False) -> tuple[str | None, str | None]:
    """(nom FR, URL de l'icône) d'une rune principale (ou d'un arbre si `style`) ; (None, None) si inconnue."""
    table = RUNE_STYLES if style else KEYSTONES
    try:
        entry = table.get(int(rune_id)) if rune_id is not None else None
    except (TypeError, ValueError):
        entry = None
    if entry is None:
        return None, None
    return entry[0], f"{DDRAGON_BASE}/cdn/img/{entry[1]}"


# Emblèmes / écussons de rang et icônes de poste : Community Dragon (assets du client LoL)
CDRAGON_STATIC_BASE = "https://raw.communitydragon.org/latest/plugins/rcp-fe-lol-static-assets/global/default"
RANKED_TIERS = frozenset(
    {"iron", "bronze", "silver", "gold", "platinum", "emerald", "diamond", "master", "grandmaster", "challenger"}
)
POSITION_ICONS: dict[str, str] = {
    "TOP": "top",
    "JUNGLE": "jungle",
    "MIDDLE": "middle",
    "BOTTOM": "bottom",
    "UTILITY": "utility",
}


def _tier_slug(tier: str | None) -> str | None:
    slug = (tier or "").strip().lower()
    return slug if slug in RANKED_TIERS else None


def rank_emblem_url(tier: str | None) -> str | None:
    """Emblème du tier (grande image) ; None si Unranked / inconnu."""
    slug = _tier_slug(tier)
    if slug is None:
        return None
    return f"{CDRAGON_STATIC_BASE}/images/ranked-emblem/emblem-{slug}.png"


def rank_mini_crest_url(tier: str | None) -> str | None:
    """Petit écusson SVG du tier ; None si Unranked / inconnu."""
    slug = _tier_slug(tier)
    if slug is None:
        return None
    return f"{CDRAGON_STATIC_BASE}/images/ranked-mini-crests/{slug}.svg"


def position_icon_url(position: str | None) -> str | None:
    """Icône SVG du poste (`teamPosition` Match-V5) ; None si inconnu."""
    slug = POSITION_ICONS.get((position or "").strip().upper())
    if slug is None:
        return None
    return f"{CDRAGON_STATIC_BASE}/svg/position-{slug}.svg"


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


def cached_champion_name(champion_id: int | None) -> str | None:
    """Identifiant image d'un champion depuis le cache déjà chargé (aucun appel réseau) ; None sinon."""
    if not champion_id:
        return None
    table = _champions_cache.get(CURRENT_VERSION) or next(iter(_champions_cache.values()), None)
    return table.get(int(champion_id)) if table else None


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
