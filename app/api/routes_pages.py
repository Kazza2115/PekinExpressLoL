"""Pages HTML (Jinja2). Les données vivantes sont chargées en JS via `/api/*`.

Chaque page reçoit : `request`, `page` (nom), `challenge` (dict), `demo_mode`,
`games_per_day` et, pour la fiche joueur, `player` (ligne `Player`).
"""

from __future__ import annotations

import hashlib

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import Challenge, Player
from app.db.session import as_utc, get_session

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _default_challenge() -> dict[str, Any]:
    """Challenge « vide » affiché tant que la ligne n'existe pas encore."""
    settings = get_settings()
    return {
        "id": None,
        "name": "Pékin Express LoL",
        "status": "registration",
        "start_at": None,
        "end_at": None,
        "games_per_day": settings.games_per_day,
        "track_flex": settings.track_flex,
    }


def _challenge_dict(challenge: Challenge | None) -> dict[str, Any]:
    """Sérialise le challenge (via `app.api.serializers` si disponible, sinon à la main)."""
    if challenge is None:
        return _default_challenge()
    try:
        from app.api.serializers import challenge_to_dict  # import paresseux : module optionnel

        data = challenge_to_dict(challenge)
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001 — on retombe sur la sérialisation locale
        pass
    status = challenge.status.value if hasattr(challenge.status, "value") else str(challenge.status)
    start_at = as_utc(challenge.start_at)
    end_at = as_utc(challenge.end_at)
    return {
        "id": challenge.id,
        "name": challenge.name,
        "status": status,
        "start_at": start_at.isoformat() if start_at else None,
        "end_at": end_at.isoformat() if end_at else None,
        "games_per_day": challenge.games_per_day,
        "track_flex": challenge.track_flex,
    }


def _load_challenge(session: Session) -> Challenge | None:
    try:
        return session.exec(select(Challenge)).first()
    except Exception:  # noqa: BLE001 — table absente (DB pas encore initialisée)
        return None


def _compute_asset_version() -> str:
    """Empreinte des fichiers statiques : change à chaque mise à jour du site, donc les
    navigateurs rechargent CSS/JS au lieu de servir une ancienne version en cache."""
    digest = hashlib.sha1()
    static_dir = Path(__file__).resolve().parent.parent / "static"
    for file in sorted(static_dir.rglob("*")):
        if file.is_file():
            digest.update(file.name.encode("utf-8"))
            digest.update(str(file.stat().st_mtime_ns).encode("utf-8"))
            digest.update(str(file.stat().st_size).encode("utf-8"))
    return digest.hexdigest()[:10]


ASSET_VERSION = _compute_asset_version()


def _context(request: Request, page: str, session: Session, **extra: Any) -> dict[str, Any]:
    settings = get_settings()
    challenge = _challenge_dict(_load_challenge(session))
    ctx: dict[str, Any] = {
        "request": request,
        "page": page,
        "challenge": challenge,
        "demo_mode": settings.demo_mode,
        "games_per_day": challenge.get("games_per_day") or settings.games_per_day,
        "asset_v": ASSET_VERSION,
        "base_url": settings.base_url,
    }
    ctx.update(extra)
    return ctx


def _render(request: Request, name: str, context: dict[str, Any], status_code: int = 200) -> Response:
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def _not_found(request: Request, session: Session, message: str | None = None) -> Response:
    ctx = _context(request, "404", session, message=message or "Cette page n'existe pas.")
    return _render(request, "404.html", ctx, status_code=404)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def home(request: Request, session: Session = Depends(get_session)) -> Response:
    return _render(request, "home.html", _context(request, "home", session))


@router.get("/duos", response_class=HTMLResponse, include_in_schema=False)
def duos(request: Request, session: Session = Depends(get_session)) -> Response:
    """Page vedette : toutes les stats par duo (composés à la main dans l'admin)."""
    return _render(request, "duos.html", _context(request, "duos", session))


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request, session: Session = Depends(get_session)) -> Response:
    return _render(request, "dashboard.html", _context(request, "dashboard", session))


@router.get("/player/{player_id}", response_class=HTMLResponse, include_in_schema=False)
def player_page(
    player_id: Annotated[int, PathParam(ge=1, le=2**31 - 1)], request: Request, session: Session = Depends(get_session)
) -> Response:
    player = session.get(Player, player_id)
    if player is None:
        return _not_found(request, session, "Ce joueur n'existe pas (ou a été supprimé).")
    ctx = _context(request, "player", session, player=player)
    return _render(request, "player.html", ctx)


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin(request: Request, session: Session = Depends(get_session)) -> Response:
    return _render(request, "admin.html", _context(request, "admin", session))


@router.get("/{path:path}", include_in_schema=False)
def catch_all(path: str, request: Request, session: Session = Depends(get_session)) -> Response:
    """404 générique : JSON pour `/api/*`, page HTML sinon (déclaré en dernier)."""
    if path.startswith("api/") or path == "api":
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return _not_found(request, session)
