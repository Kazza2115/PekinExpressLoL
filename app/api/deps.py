"""Dépendances FastAPI communes aux routes JSON : mot de passe admin, challenge unique."""

from __future__ import annotations

import asyncio
import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import Challenge
from app.db.session import get_session

ADMIN_ERROR_DETAIL = "Mot de passe administrateur incorrect."


# Délai imposé après un mot de passe refusé : rend la force brute inintéressante
FAILED_AUTH_DELAY_S = 0.5


def check_admin_password(provided: str | None) -> bool:
    """Compare en temps constant avec `ADMIN_PASSWORD`."""
    expected = get_settings().admin_password
    value = provided or ""
    return bool(value) and secrets.compare_digest(value.encode("utf-8"), expected.encode("utf-8"))


async def require_admin(x_admin_password: str | None = Header(default=None)) -> None:
    """Vérifie l'en-tête `X-Admin-Password` → 401 (après un court délai) sinon."""
    if not check_admin_password(x_admin_password):
        await asyncio.sleep(FAILED_AUTH_DELAY_S)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=ADMIN_ERROR_DETAIL)


def get_challenge(session: Session = Depends(get_session)) -> Challenge:
    """La ligne unique `Challenge` (créée à la volée si elle manque)."""
    challenge = session.exec(select(Challenge).order_by(Challenge.id)).first()
    if challenge is None:
        # Import tardif : bootstrap dépend des services, pas l'inverse
        from app.services.bootstrap import ensure_challenge

        challenge = ensure_challenge(session)
    return challenge
