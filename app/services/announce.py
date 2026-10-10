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


# --------------------------------------------------------------------------- #
# « Placements terminés » : une annonce par duo, quand ses joueurs ont tous un rang
# --------------------------------------------------------------------------- #


async def announce_placements(*, settings: Settings | None = None, now: datetime | None = None) -> int:
    """Annonce chaque duo qui vient de finir ses placements (un de ses joueurs était non classé et
    tous ont maintenant un rang Solo/Duo). Une seule fois par duo. Renvoie le nombre d'annonces."""
    from app.api.leaderboard import build_leaderboard  # import local : pas de cycle services → api
    from app.db.models import MatchParticipant, Queue, RankSnapshot, game_end_of
    from app.services.stats import absolute_lp, format_rank, label_from_absolute_lp

    settings = settings if settings is not None else get_settings()
    now = now or utcnow()
    if not (settings.discord_webhook_url or "").strip():
        return 0  # pas de Discord : rien d'annoncé, les duos restent à annoncer
    pending: list[tuple[int, tuple[str, list[dict[str, Any]]]]] = []
    try:
        with session_scope() as session:
            challenge = session.exec(select(Challenge).order_by(col(Challenge.id))).first()
            if challenge is None or challenge.status != ChallengeStatus.RUNNING:
                return 0
            teams = [t for t in session.exec(select(Team)).all() if t.placements_announced_at is None]
            if not teams:
                return 0
            players = session.exec(select(Player).where(Player.active == True)).all()  # noqa: E712
            ranking = None
            for team in teams:
                members = [p for p in players if p.team_id == team.id and p.id is not None]
                if not members:
                    continue
                infos: list[notifications.PlacementMember] = []
                had_placements = False
                ranked_values: list[int] = []
                for player in members:
                    snaps = session.exec(
                        select(RankSnapshot)
                        .where(RankSnapshot.player_id == player.id, RankSnapshot.queue == Queue.SOLO)
                        .order_by(col(RankSnapshot.captured_at))
                    ).all()
                    if not snaps or snaps[-1].tier is None:
                        break  # encore en placement (ou jamais vu) : le duo n'a pas fini
                    latest = snaps[-1]
                    placed = any(s.tier is None for s in snaps)
                    had_placements = had_placements or placed
                    wins = losses = 0
                    if placed:
                        # Bilan des placements : parties classées terminées avant le premier rang obtenu
                        first_ranked = next(as_utc(s.captured_at) for s in snaps if s.tier is not None)
                        start = as_utc(challenge.start_at)
                        for part in session.exec(
                            select(MatchParticipant).where(
                                MatchParticipant.player_id == player.id, MatchParticipant.queue == Queue.SOLO
                            )
                        ).all():
                            ended = game_end_of(part)
                            if part.is_remake or ended > first_ranked or (start is not None and ended < start):  # type: ignore[operator]
                                continue
                            wins += int(bool(part.win))
                            losses += int(not part.win)
                    value = absolute_lp(latest.tier, latest.rank, latest.lp)
                    if value is not None:
                        ranked_values.append(value)
                    infos.append(
                        notifications.PlacementMember(
                            name=player.display_name,
                            rank_label=format_rank(latest.tier, latest.rank, latest.lp),
                            tier=latest.tier,
                            placed=placed,
                            wins=wins,
                            losses=losses,
                        )
                    )
                else:
                    if not had_placements:
                        continue  # duo classé dès le départ : pas de placements à annoncer
                    if ranking is None:
                        ranking, _ = build_leaderboard(session, challenge, now=now)
                    team_stats = next((t for t in ranking if t.team_id == team.id), None)
                    average = round(sum(ranked_values) / len(ranked_values)) if ranked_values else None
                    pending.append(
                        (
                            int(team.id),  # type: ignore[arg-type]
                            notifications.build_placements_announcement(
                                team.name,
                                team.color,
                                infos,
                                average_rank=label_from_absolute_lp(average) if average is not None else None,
                                position=team_stats.position if team_stats is not None else None,
                                teams_count=len(ranking),
                                lp_net=team_stats.lp_net if team_stats is not None else None,
                                settings=settings,
                            ),
                        )
                    )
        sent = 0
        for team_id, (content, embeds) in pending:
            if not await notifications.send_discord(content, embeds=embeds, settings=settings):
                continue  # nouvel essai au prochain cycle
            with session_scope() as session:
                team = session.get(Team, team_id)
                if team is not None:
                    team.placements_announced_at = now
                    session.add(team)
                    session.commit()
                    bus.publish("placements_done", {"team_id": team_id, "team_name": team.name})
            sent += 1
        return sent
    except Exception:  # noqa: BLE001 — jamais bloquant pour le suivi des parties
        log.warning("Annonce des placements impossible", exc_info=True)
        return 0
