"""Point d'entrée FastAPI : `uvicorn app.main:app --reload`.

- monte les routes JSON (`app.api.routes_api`, `routes_admin`) et HTML (`routes_pages`)
- démarre le poller en tâche de fond au démarrage (lifespan)
- charge `players.yaml` si la table des joueurs est vide
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import PROJECT_ROOT, get_settings
from app.db.session import init_db, session_scope
from app.events import bus
from app.riot import get_api
from app.services.bootstrap import backfill_match_details, ensure_challenge, load_players_yaml
from app.services.poller import Poller
from app.state import state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pekin")
# Une ligne par requête HTTP, c'est trop : seules les erreurs httpx sont loguées
logging.getLogger("httpx").setLevel(logging.WARNING)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    init_db()
    ensure_challenge()
    # Colonnes de détail de partie ajoutées après coup : ré-extraites du JSON Match-V5 déjà stocké
    with session_scope() as session:
        backfilled = backfill_match_details(session)
    if backfilled:
        log.info("Détails de partie complétés pour %d participation(s) existante(s)", backfilled)
    await load_players_yaml(PROJECT_ROOT / "players.yaml")
    api = get_api()
    poller = Poller(api=api, bus=bus, state=state)
    app.state.poller = poller
    app.state.riot_api = api
    # PEKIN_DISABLE_POLLER=1 (tests) : pas de tâche de fond
    task: asyncio.Task | None = None
    if os.getenv("PEKIN_DISABLE_POLLER", "") not in {"1", "true"}:
        task = asyncio.create_task(poller.run_forever(), name="poller")
    if settings.admin_password_is_default:
        log.warning(
            "ADMIN_PASSWORD n'est pas défini : le mot de passe organisateur est « %s ». "
            "Change-le dans .env avant de partager l'URL du site.",
            settings.admin_password,
        )
    log.info(
        "Pékin Express LoL démarré (%s, polling toutes les %ss)",
        "MODE DÉMO" if settings.demo_mode else f"API Riot {settings.riot_platform}",
        settings.poll_interval_seconds,
    )
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await api.aclose()


SLOW_REQUEST_S = 2.0


def create_app() -> FastAPI:
    app = FastAPI(title="Pékin Express LoL", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.middleware("http")
    async def log_slow_requests(request, call_next):  # noqa: ANN001
        """Toute requête de plus de SLOW_REQUEST_S est signalée (diagnostic de lenteur)."""
        started = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - started
        if elapsed > SLOW_REQUEST_S and request.url.path != "/api/events":
            log.warning("Requête lente : %s %s en %.1f s", request.method, request.url.path, elapsed)
        return response

    from app.api.routes_admin import router as admin_router
    from app.api.routes_api import router as api_router
    from app.api.routes_pages import router as pages_router

    app.include_router(api_router)
    app.include_router(admin_router)
    app.include_router(pages_router)
    return app


app = create_app()
