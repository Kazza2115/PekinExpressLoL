"""Dépendances FastAPI communes aux routes JSON : mot de passe admin, challenge unique."""

from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import Challenge
from app.db.session import get_session

ADMIN_ERROR_DETAIL = "Mot de passe administrateur incorrect."


def require_admin(x_admin_password: str | None = Header(default=None)) -> None:
    """Vérifie l'en-tête `X-Admin-Password` (comparaison en temps constant) → 401 sinon."""
    expected = get_settings().admin_password
    provided = x_admin_password or ""
    if not provided or not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=ADMIN_ERROR_DETAIL)


def get_challenge(session: Session = Depends(get_session)) -> Challenge:
    """La ligne unique `Challenge` (créée à la volée si elle manque)."""
    challenge = session.exec(select(Challenge).order_by(Challenge.id)).first()
    if challenge is None:
        # Import tardif : bootstrap dépend des services, pas l'inverse
        from app.services.bootstrap import ensure_challenge

        challenge = ensure_challenge(session)
    return challenge
