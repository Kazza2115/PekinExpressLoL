"""Lecture de la configuration depuis `.env` et les variables d'environnement.

La clé Riot n'est jamais exposée côté client : seul le backend lit `Settings`.
`reload_settings()` permet de relire `.env` à chaud (changement de clé sans redémarrer).
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

log = logging.getLogger("pekin.config")

# Racine du projet (dossier contenant `app/`)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
DEFAULT_ADMIN_PASSWORD = "change-me"


def _env_bool(name: str, default: bool | None = None) -> bool | None:
    """Interprète une variable d'environnement booléenne (vide → default)."""
    raw = os.getenv(name, "")
    if raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# GIF Discord : catégories cherchées sur Klipy (une tirée au hasard à chaque résultat)
DEFAULT_GIF_SEARCH_LOSS: tuple[str, ...] = (
    "Monkey",
    "Goofy Dog",
    "Charlie Kirk",
    "Goofy Patrick",
    "Spongebob cursed",
    "Indian Goofy",
)
DEFAULT_GIF_SEARCH_WIN: tuple[str, ...] = (
    "Chad",
    "Lightskin",
    "Extreme lightskin",
    "Handsome spongebob",
    "Happy Netanyahu",
    "Goofy Drake",
)
# GIF de l'annonce du début du challenge (page Klipy ou lien direct d'image)
DEFAULT_START_GIF = "https://klipy.com/gifs/sponge-bob-bob-esponja"
# Valeurs qui désactivent une liste de GIF (`DISCORD_GIF_… = off`)
GIF_OFF_VALUES = {"off", "non", "aucun", "none", "0", "-"}
_GIPHY_PAGE_RE = re.compile(r"^https?://(?:www\.)?giphy\.com/(?:gifs|stickers)/(?:[^/?#]*-)?([A-Za-z0-9]+)/?(?:[?#].*)?$")
# Identifiant de rôle Discord seul, ou mention de rôle « <@&123…> » (snowflake de 17 à 20 chiffres)
_ROLE_ID_RE = re.compile(r"\s*(?:<@&)?(\d{15,21})>?\s*")


def normalize_gif_url(url: str) -> str | None:
    """Lien de GIF utilisable dans un message Discord, ou None.

    Une page GIPHY (« giphy.com/gifs/nom-ID ») devient le lien direct du GIF ; les autres liens
    (media.giphy.com, media.tenor.com, …/image.gif) sont gardés tels quels.
    """
    url = url.strip().strip("<>")
    if not re.match(r"^https?://", url):
        return None
    page = _GIPHY_PAGE_RE.match(url)
    if page:
        return f"https://media.giphy.com/media/{page.group(1)}/giphy.gif"
    return url


def _env_gifs(name: str) -> tuple[str, ...]:
    """Liens de GIF (séparés par des espaces, virgules ou points-virgules) ; vide ou `off` → aucun."""
    raw = os.getenv(name, "").strip()
    if not raw or raw.lower() in GIF_OFF_VALUES:
        return ()
    urls = (normalize_gif_url(part) for part in re.split(r"[\s,;]+", raw) if part)
    return tuple(url for url in urls if url)


def _env_terms(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Catégories de recherche (séparées par des virgules ou points-virgules) ; vide → défaut, `off` → aucune."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    if raw.lower() in GIF_OFF_VALUES:
        return ()
    return tuple(term.strip() for term in re.split(r"[,;\n]+", raw) if term.strip())


def _env_start_gif() -> str:
    """GIF de l'annonce du début : DISCORD_GIF_START (vide → celui par défaut, `off` → aucun)."""
    raw = os.getenv("DISCORD_GIF_START", "").strip()
    if not raw:
        return DEFAULT_START_GIF
    return "" if raw.lower() in GIF_OFF_VALUES else raw


def parse_role_id(raw: str) -> str:
    """ID du rôle Discord à mentionner : « 1234… » ou « <@&1234…> » → « 1234… » ; sinon vide.

    Un lien de salon, une mention de membre (« <@123> ») ou de salon (« <#123> ») est refusé :
    ses chiffres ne sont pas ceux d'un rôle.
    """
    match = _ROLE_ID_RE.fullmatch(raw or "")
    return match.group(1) if match else ""


def _invalid_role_id(raw: str) -> str:
    """Valeur non vide de DISCORD_ROLE_ID qui n'est pas un identifiant de rôle (sinon vide)."""
    raw = (raw or "").strip()
    if raw and not parse_role_id(raw):
        log.warning("DISCORD_ROLE_ID invalide (%r) : attendu l'identifiant numérique du rôle", raw[:40])
        return raw[:80]
    return ""


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        return int(raw) if raw.strip() else default
    except ValueError:
        return default


@dataclass
class Settings:
    riot_api_key: str
    riot_platform: str
    riot_region: str
    demo_mode: bool
    poll_interval_seconds: int
    track_flex: bool
    admin_password: str
    database_url: str
    games_per_day: int
    max_players: int
    timezone: str
    discord_webhook_url: str
    base_url: str
    # Détection des parties en cours (Spectator seul) entre deux cycles complets ; >= poll = désactivée
    live_poll_seconds: int = 30
    # Lien fixe GitHub Pages : jeton (accès Contents en écriture au dépôt), dépôt et branche
    # où publier l'adresse actuelle du site (docs/site.json). Jeton vide = désactivé.
    github_token: str = ""
    github_repo: str = "Kazza2115/PekinExpressLoL"
    github_branch: str = "claude/quirky-pascal-y84evb"
    # Dates par défaut du challenge (jj/mm/aaaa hh:mm, fuseau TIMEZONE), appliquées tant qu'aucune
    # date n'est enregistrée et que la fin est à venir. Vide = pas de date par défaut.
    challenge_start: str = "10/10/2026 09:00"
    challenge_end: str = "12/10/2026 00:00"
    # Discord : rôle mentionné dans chaque message (ID numérique, vide = aucune mention)
    discord_role_id: str = ""
    # Valeur de DISCORD_ROLE_ID refusée (lien, mention de membre…) : affichée par le test de l'admin
    discord_role_id_invalid: str = ""
    # GIF des résultats : catégories cherchées sur Klipy (clé gratuite KLIPY_API_KEY), tirées au
    # hasard ; liens de secours si Klipy est indisponible (ou sans clé)
    klipy_api_key: str = ""
    gif_search_win: tuple[str, ...] = DEFAULT_GIF_SEARCH_WIN
    gif_search_loss: tuple[str, ...] = DEFAULT_GIF_SEARCH_LOSS
    gif_fallback_win: tuple[str, ...] = ()
    gif_fallback_loss: tuple[str, ...] = ()
    gif_start: str = DEFAULT_START_GIF  # annonce « c'est parti » à l'heure du début ; vide = sans GIF
    # Fuseau résolu une seule fois (`tz` est lu pour chaque joueur à chaque requête de stats)
    _tz: tzinfo | None = field(default=None, init=False, repr=False, compare=False)
    _tz_fallback: bool = field(default=False, init=False, repr=False, compare=False)

    @property
    def tz(self) -> tzinfo:
        """Fuseau horaire utilisé pour découper les journées du challenge.

        Sous Windows, Python n'a pas de base de fuseaux système : sans le paquet `tzdata`,
        `ZoneInfo("Europe/Paris")` échoue. Plutôt qu'une erreur 500 sur toutes les pages de
        stats, on se replie sur UTC (journalisé une seule fois, signalé par `tz_fallback`).
        """
        if self._tz is None:
            try:
                self._tz = ZoneInfo(self.timezone)
            except ZoneInfoNotFoundError:
                log.warning(
                    "Fuseau %s introuvable (paquet tzdata manquant ?) : repli sur UTC — "
                    "les journées du challenge sont découpées à minuit UTC",
                    self.timezone,
                )
                self._tz = timezone.utc
                self._tz_fallback = True
        return self._tz

    @property
    def tz_fallback(self) -> bool:
        """True si le fuseau configuré est introuvable (`tz` vaut alors UTC)."""
        _ = self.tz  # force la résolution (et l'avertissement) si elle n'a pas encore eu lieu
        return self._tz_fallback

    @property
    def platform_host(self) -> str:
        return f"https://{self.riot_platform}.api.riotgames.com"

    @property
    def region_host(self) -> str:
        return f"https://{self.riot_region}.api.riotgames.com"

    @property
    def has_api_key(self) -> bool:
        return bool(self.riot_api_key.strip())

    @property
    def admin_password_is_default(self) -> bool:
        return self.admin_password == DEFAULT_ADMIN_PASSWORD


def _build_settings() -> Settings:
    load_dotenv(ENV_FILE, override=True)
    api_key = os.getenv("RIOT_API_KEY", "").strip()
    # Sans clé Riot → mode démo automatique (client simulé, aucun appel réseau)
    demo_mode = _env_bool("DEMO_MODE", default=not bool(api_key))
    assert demo_mode is not None
    default_poll = 10 if demo_mode else 90
    return Settings(
        riot_api_key=api_key,
        riot_platform=os.getenv("RIOT_PLATFORM", "euw1").strip() or "euw1",
        riot_region=os.getenv("RIOT_REGION", "europe").strip() or "europe",
        demo_mode=demo_mode,
        poll_interval_seconds=max(3, _env_int("POLL_INTERVAL_SECONDS", default_poll)),
        track_flex=bool(_env_bool("TRACK_FLEX", default=False)),
        admin_password=os.getenv("ADMIN_PASSWORD", "").strip() or DEFAULT_ADMIN_PASSWORD,
        database_url=os.getenv("DATABASE_URL", "sqlite:///./data/tracker.db").strip()
        or "sqlite:///./data/tracker.db",
        games_per_day=max(1, _env_int("GAMES_PER_DAY", 10)),
        max_players=max(2, _env_int("MAX_PLAYERS", 8)),
        timezone=os.getenv("TIMEZONE", "Europe/Paris").strip() or "Europe/Paris",
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", "").strip(),
        base_url=(os.getenv("BASE_URL", "http://localhost:8000").strip() or "http://localhost:8000").rstrip("/"),
        # Minimum 10 s : 8 joueurs × 1 requête toutes les 10 s reste sous la limite d'une clé de dev
        live_poll_seconds=max(10, _env_int("LIVE_POLL_SECONDS", 30)),
        github_token=os.getenv("GITHUB_TOKEN", "").strip(),
        github_repo=os.getenv("GITHUB_REPO", "").strip() or "Kazza2115/PekinExpressLoL",
        github_branch=os.getenv("GITHUB_BRANCH", "").strip() or "claude/quirky-pascal-y84evb",
        challenge_start=os.getenv("CHALLENGE_START", "10/10/2026 09:00").strip(),
        challenge_end=os.getenv("CHALLENGE_END", "12/10/2026 00:00").strip(),
        discord_role_id=parse_role_id(os.getenv("DISCORD_ROLE_ID", "")),
        discord_role_id_invalid=_invalid_role_id(os.getenv("DISCORD_ROLE_ID", "")),
        klipy_api_key=os.getenv("KLIPY_API_KEY", "").strip(),
        gif_search_win=_env_terms("DISCORD_GIF_SEARCH_WIN", DEFAULT_GIF_SEARCH_WIN),
        gif_search_loss=_env_terms("DISCORD_GIF_SEARCH_LOSS", DEFAULT_GIF_SEARCH_LOSS),
        gif_fallback_win=_env_gifs("DISCORD_GIF_WIN"),
        gif_fallback_loss=_env_gifs("DISCORD_GIF_LOSS"),
        gif_start=_env_start_gif(),
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Settings en cache (lues une fois, rechargeables via `reload_settings`)."""
    global _settings
    if _settings is None:
        _settings = _build_settings()
    return _settings


def reload_settings() -> Settings:
    """Relit `.env` (ex. nouvelle clé Riot) sans redémarrer le serveur."""
    global _settings
    _settings = _build_settings()
    return _settings
