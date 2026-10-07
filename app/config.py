"""Lecture de la configuration depuis `.env` et les variables d'environnement.

La clé Riot n'est jamais exposée côté client : seul le backend lit `Settings`.
`reload_settings()` permet de relire `.env` à chaud (changement de clé sans redémarrer).
"""

from __future__ import annotations

import logging
import os
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
