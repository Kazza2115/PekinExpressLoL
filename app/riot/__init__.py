"""Singleton du client Riot : `DemoRiotClient` en mode démo, `RiotClient` sinon.

`tests/conftest.py` injecte directement le client de test ici (`riot_pkg._api = demo_api`).
"""

from __future__ import annotations

from app.riot.base import RiotAPI

_api: RiotAPI | None = None


def get_api() -> RiotAPI:
    """Client Riot partagé (créé au premier appel selon `settings.demo_mode`)."""
    global _api
    if _api is None:
        from app.config import get_settings

        if get_settings().demo_mode:
            from app.riot.demo import DemoRiotClient

            _api = DemoRiotClient()
        else:
            from app.riot.client import RiotClient

            _api = RiotClient()
    return _api


def reset_api() -> None:
    """Oublie le singleton (sans le fermer : les tests / `reload-settings` gèrent)."""
    global _api
    _api = None


__all__ = ["RiotAPI", "get_api", "reset_api"]
