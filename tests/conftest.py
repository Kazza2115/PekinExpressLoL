"""Fixtures communes : SQLite en mémoire, client Riot démo déterministe, TestClient sans poller."""

from __future__ import annotations

import os

# Doit être positionné AVANT l'import de `app.*` (lecture des settings au premier appel)
os.environ.setdefault("DEMO_MODE", "1")
os.environ.setdefault("RIOT_API_KEY", "")
os.environ.setdefault("ADMIN_PASSWORD", "test-password")
os.environ.setdefault("PEKIN_DISABLE_POLLER", "1")
os.environ.setdefault("DISCORD_WEBHOOK_URL", "")
os.environ.setdefault("DATABASE_URL", "sqlite://")
# Pas de dates de challenge par défaut en test (testées à part dans test_default_schedule.py)
os.environ.setdefault("CHALLENGE_START", "")
os.environ.setdefault("CHALLENGE_END", "")

import random  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from app.config import reload_settings  # noqa: E402
from app.db import session as db_session  # noqa: E402
from app.events import bus  # noqa: E402
from app.state import state  # noqa: E402

ADMIN_PASSWORD = "test-password"


@pytest.fixture(autouse=True)
def _settings():
    """Settings relues avec les variables de test (mode démo, mot de passe connu)."""
    os.environ["DEMO_MODE"] = "1"
    os.environ["RIOT_API_KEY"] = ""
    os.environ["ADMIN_PASSWORD"] = ADMIN_PASSWORD
    os.environ["PEKIN_DISABLE_POLLER"] = "1"
    os.environ["DISCORD_WEBHOOK_URL"] = ""
    os.environ["DISCORD_ROLE_ID"] = ""
    os.environ["KLIPY_API_KEY"] = ""
    os.environ["DISCORD_GIF_START"] = "off"  # pas d'appel réseau vers Klipy pendant les tests
    os.environ["DATABASE_URL"] = "sqlite://"
    os.environ["CHALLENGE_START"] = ""
    os.environ["CHALLENGE_END"] = ""
    yield reload_settings()


@pytest.fixture()
def engine():
    """Moteur SQLite en mémoire partagé par toutes les sessions du test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    db_session.set_engine(engine)
    # État mémoire propre
    state.live_games.clear()
    state.last_poll = None
    state.poll_count = 0
    state.polling = False
    bus._history.clear()  # noqa: SLF001
    from app.api import routes_api

    routes_api._write_hits.clear()  # noqa: SLF001 — garde-fou anti-spam remis à zéro
    from app.presence import presence

    presence.clear()  # personne de connecté au début de chaque test
    yield engine
    db_session.set_engine(None)
    engine.dispose()


@pytest.fixture()
def session(engine):
    with Session(engine) as session:
        yield session


@pytest.fixture()
def demo_api():
    """Client Riot simulé, déterministe : chaque joueur démarre une partie à chaque tick, durée nulle."""
    from app.riot import reset_api
    from app.riot.demo import DemoRiotClient

    reset_api()
    api = DemoRiotClient(start_chance=1.0, game_duration_range=(0, 0), rng=random.Random(42))
    yield api
    reset_api()


@pytest.fixture()
def client(engine, demo_api):
    """TestClient FastAPI (lifespan exécuté, poller désactivé, client démo injecté)."""
    from fastapi.testclient import TestClient

    from app import riot as riot_pkg
    from app.main import app

    riot_pkg._api = demo_api  # noqa: SLF001 — singleton remplacé par le client de test
    with TestClient(app) as test_client:
        yield test_client
    riot_pkg._api = None  # noqa: SLF001


@pytest.fixture()
def admin_headers():
    return {"X-Admin-Password": ADMIN_PASSWORD}
