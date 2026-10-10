"""Annonce Discord « c'est parti » à l'heure du début du challenge.

Le poller vérifie à chaque passage (toutes les 30 s environ) : dès que le challenge est en cours
et que son heure de début est passée, le message part une seule fois (rôle mentionné, duos, GIF
choisi par l'organisateur). Si le site était éteint à l'heure du début, l'annonce part à son
démarrage, jusqu'à `ANNOUNCE_MAX_DELAY` après ; au-delà, elle est abandonnée. Démarrer le
challenge à la main sans date de début l'annonce aussitôt. L'Admin peut aussi l'envoyer à la main
(« 📣 Annoncer le début »), avec le diagnostic de `announce_status`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import col, select

from app.config import Settings, get_settings
from app.db.models import Challenge, ChallengeStatus, Player, Team, utcnow
from app.db.session import as_utc, session_scope
from app.events import bus
from app.services import gifs, notifications

log = logging.getLogger(__name__)

ANNOUNCE_MAX_DELAY = timedelta(hours=3)
_lock = asyncio.Lock()


def _due(challenge: Challenge | None, now: datetime) -> bool:
    if challenge is None or challenge.status != ChallengeStatus.RUNNING:
        return False
    start = as_utc(challenge.start_at)
    if start is None or now < start:
        return False
    done = as_utc(challenge.start_announced_at)
    return done is None or done < start  # nouveau début programmé : nouvelle annonce


def announce_status(*, settings: Settings | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Où en est l'annonce du début (affiché par l'Admin) : état, dates et explication."""
    settings = settings if settings is not None else get_settings()
    now = now or utcnow()
    with session_scope() as session:
        challenge = session.exec(select(Challenge).order_by(col(Challenge.id))).first()
        status = challenge.status if challenge is not None else None
        start = as_utc(challenge.start_at) if challenge is not None else None
        done = as_utc(challenge.start_announced_at) if challenge is not None else None
        dropped = bool(challenge.start_announce_dropped) if challenge is not None else False
    fmt = lambda d: d.astimezone(settings.tz).strftime("%d/%m/%Y %H:%M") if d is not None else None  # noqa: E731
    base = {"status": getattr(status, "value", status), "start_at": fmt(start), "announced_at": fmt(done),
            "webhook": bool((settings.discord_webhook_url or "").strip())}
    if status != ChallengeStatus.RUNNING:
        return base | {"state": "not_running", "message": "Le challenge n'est pas démarré : clique « 🚀 Démarrer » (le début programmé est gardé)."}
    if start is None:
        return base | {"state": "no_start", "message": "Pas d'heure de début enregistrée."}
    if done is not None and done >= start:
        return base | {"state": "dropped" if dropped else "sent", "message": (
            f"Abandonnée : le site a vu le début ({fmt(start)}) plus de 3 h après. Envoie-la à la main."
            if dropped else f"Envoyée le {fmt(done)}.")}
    if now < start:
        return base | {"state": "pending", "message": f"Partira automatiquement le {fmt(start)}."}
    if not base["webhook"]:
        return base | {"state": "no_webhook", "message": "DISCORD_WEBHOOK_URL est vide dans .env."}
    return base | {"state": "due", "message": "Début passé : l'annonce part au prochain passage du site (30 s)."}


async def _send(settings: Settings, challenge_id_now: datetime) -> notifications.DiscordSendResult:
    """Construit et envoie l'annonce (duos, GIF), puis note la date d'envoi si Discord l'a acceptée."""
    with session_scope() as session:
        challenge = session.exec(select(Challenge).order_by(col(Challenge.id))).first()
        if challenge is None:
            return notifications.DiscordSendResult(sent=False, error="aucun challenge")
        teams = session.exec(select(Team).order_by(col(Team.slot), col(Team.id))).all()
        players = session.exec(select(Player).where(Player.active == True)).all()  # noqa: E712
        lineup = [(team.name, [p.display_name for p in players if p.team_id == team.id]) for team in teams]
        name, games_per_day, end_at = challenge.name, challenge.games_per_day, challenge.end_at
    gif = await gifs.resolve_gif(settings.gif_start, settings=settings) if settings.gif_start else None
    content, embeds = notifications.build_start_announcement(
        name, games_per_day, end_at, lineup, gif=gif, gif_page=settings.gif_start or None, settings=settings
    )
    result = await notifications.post_discord(content, embeds=embeds, settings=settings)
    if result.sent:
        with session_scope() as session:
            challenge = session.exec(select(Challenge).order_by(col(Challenge.id))).first()
            if challenge is not None:
                challenge.start_announced_at = challenge_id_now
                challenge.start_announce_dropped = False
                session.add(challenge)
                session.commit()
        bus.publish("challenge_begins", {"name": name})
        log.info("Annonce Discord du début du challenge envoyée")
    else:
        log.warning("Annonce du début : %s", result.error or "envoi refusé")
    return result


async def announce_start_if_due(*, settings: Settings | None = None, now: datetime | None = None) -> bool:
    """Envoie l'annonce du début si c'est l'heure et qu'elle n'est pas déjà partie. Jamais d'exception."""
    settings = settings if settings is not None else get_settings()
    now = now or utcnow()
    try:
        async with _lock:
            with session_scope() as session:
                challenge = session.exec(select(Challenge).order_by(col(Challenge.id))).first()
                if not _due(challenge, now):
                    return False
                assert challenge is not None
                if not (settings.discord_webhook_url or "").strip():
                    return False  # pas de Discord : rien d'envoyé, l'annonce reste à faire
                if now - as_utc(challenge.start_at) > ANNOUNCE_MAX_DELAY:  # type: ignore[operator]
                    # Trop tard pour dire « ça commence » : abandonnée (l'Admin peut l'envoyer à la main)
                    challenge.start_announced_at = now
                    challenge.start_announce_dropped = True
                    session.add(challenge)
                    session.commit()
                    log.info("Annonce du début abandonnée : le début est passé depuis plus de 3 h")
                    return False
            result = await _send(settings, now)
            return result.sent
    except Exception:  # noqa: BLE001 — l'annonce ne doit jamais casser le suivi des parties
        log.warning("Annonce du début impossible", exc_info=True)
        return False


async def announce_start_now(*, settings: Settings | None = None) -> dict[str, Any]:
    """Envoi à la main depuis l'Admin, quel que soit l'état ; renvoie le résultat et le diagnostic."""
    settings = settings if settings is not None else get_settings()
    before = announce_status(settings=settings)
    async with _lock:
        result = await _send(settings, utcnow())
    return {"sent": result.sent, "error": result.error, "before": before, "after": announce_status(settings=settings)}
