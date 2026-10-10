"""Annonce Discord « c'est parti » à l'heure du début du challenge.

Le poller vérifie à chaque passage (toutes les 30 s environ) : dès que le challenge est en cours
et que son heure de début est passée, le message part une seule fois (rôle mentionné, duos, GIF
choisi par l'organisateur). Si le site était éteint à l'heure du début, l'annonce part à son
démarrage, jusqu'à `ANNOUNCE_MAX_DELAY` après ; au-delà, elle est abandonnée. Démarrer le
challenge à la main sans date de début l'annonce aussitôt.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

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
                late = now - as_utc(challenge.start_at) > ANNOUNCE_MAX_DELAY  # type: ignore[operator]
                if late or not (settings.discord_webhook_url or "").strip():
                    # Trop tard pour dire « ça commence » (ou pas de Discord) : on n'annonce plus
                    challenge.start_announced_at = now
                    session.add(challenge)
                    session.commit()
                    if late:
                        log.info("Annonce du début abandonnée : le début est passé depuis plus de 3 h")
                    return False
                teams = session.exec(select(Team).order_by(col(Team.slot), col(Team.id))).all()
                players = session.exec(select(Player).where(Player.active == True)).all()  # noqa: E712
                lineup = [
                    (team.name, [p.display_name for p in players if p.team_id == team.id])
                    for team in teams
                ]
                name, games_per_day, end_at = challenge.name, challenge.games_per_day, challenge.end_at
            gif = await gifs.resolve_gif(settings.gif_start, settings=settings) if settings.gif_start else None
            content, embeds = notifications.build_start_announcement(
                name, games_per_day, end_at, lineup, gif=gif, gif_page=settings.gif_start or None, settings=settings
            )
            sent = await notifications.send_discord(content, embeds=embeds, settings=settings)
            if not sent:
                log.warning("Annonce du début : envoi Discord refusé, nouvel essai au prochain passage")
                return False
            with session_scope() as session:
                challenge = session.exec(select(Challenge).order_by(col(Challenge.id))).first()
                if challenge is not None:
                    challenge.start_announced_at = now
                    session.add(challenge)
                    session.commit()
            bus.publish("challenge_begins", {"name": name})
            log.info("Annonce Discord du début du challenge envoyée")
            return True
    except Exception:  # noqa: BLE001 — l'annonce ne doit jamais casser le suivi des parties
        log.warning("Annonce du début impossible", exc_info=True)
        return False
