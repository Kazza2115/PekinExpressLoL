"""Routes d'administration (`/api/admin/...`), protégées par l'en-tête `X-Admin-Password`.

Toutes les routes sont asynchrones : elles publient sur le bus d'événements (qui vit
sur la boucle) ou appellent le poller.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func
from sqlmodel import Session, col, select

from app.api.deps import get_challenge, require_admin
from app.api.serializers import challenge_to_dict, player_public, team_public
from app.config import get_settings, reload_settings
from app.db.models import Challenge, ChallengeStatus, Match, MatchParticipant, Player, Queue, RankSnapshot, Team, utcnow
from app.db.session import get_session
from app.events import bus
from app.riot import get_api, reset_api
from app.services import notifications
from app.services.draw import perform_draw
from app.state import state

log = logging.getLogger("pekin.admin")

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DRAW_FIRST_DETAIL = "Tire d'abord les duos."
TEAM_NOT_FOUND_DETAIL = "Duo introuvable."
PLAYER_NOT_FOUND_DETAIL = "Joueur introuvable."
TEST_NOTIFICATION_CONTENT = "🔔 Test de notification — Pékin Express LoL : le webhook Discord fonctionne."


# ---------------------------------------------------------------------------
# Corps de requêtes
# ---------------------------------------------------------------------------


class StartIn(BaseModel):
    start_at: str | None = None  # ISO 8601 ; défaut : maintenant


class ResetIn(BaseModel):
    keep_players: bool = True


class ChallengePatch(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    games_per_day: int | None = Field(default=None, ge=1, le=100)
    start_at: str | None = None  # "" ou null → efface
    end_at: str | None = None
    track_flex: bool | None = None


class TeamPatch(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    color: str | None = None
    window_start: str | None = None
    window_end: str | None = None


class PlayerPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=20)
    active: bool | None = None
    team_id: int | None = Field(default=None, ge=1, le=2**31 - 1)  # null explicite → retire du duo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Attente maximale du cycle de démarrage dans la réponse HTTP (secondes)
START_POLL_TIMEOUT_S = 25
# Bornes des dates saisies (évite les OverflowError de `astimezone`)
MIN_YEAR, MAX_YEAR = 2000, 2100
# Identifiants : jamais au-delà de 2^31 (SQLite / sens pratique)
EntityId = Annotated[int, Path(ge=1, le=2**31 - 1)]


def parse_datetime(value: str | None, field: str) -> datetime | None:
    """ISO 8601 → datetime UTC ; naïf → interprété dans `settings.tz` ; vide → None."""
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
        if not MIN_YEAR <= parsed.year <= MAX_YEAR:
            raise ValueError("année hors limites")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=get_settings().tz)
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Date invalide pour « {field} » : {value}"
        ) from exc


def _last_solo_snapshot(session: Session, player_id: int) -> RankSnapshot | None:
    return session.exec(
        select(RankSnapshot)
        .where(col(RankSnapshot.player_id) == player_id, col(RankSnapshot.queue) == Queue.SOLO)
        .order_by(col(RankSnapshot.captured_at).desc(), col(RankSnapshot.id).desc())
    ).first()


def _team_player_ids(session: Session, team_id: int) -> list[int]:
    ids = session.exec(select(Player.id).where(col(Player.team_id) == team_id).order_by(col(Player.id))).all()
    return [pid for pid in ids if pid is not None]


def _drop_live_game(player: Player) -> None:
    """Retire la partie en cours d'un joueur de l'état mémoire en prévenant les clients SSE."""
    live = state.live_games.pop(player.id, None)
    if live is None:
        return
    bus.publish(
        "live_end",
        {
            "player_id": player.id,
            "display_name": player.display_name,
            "team_id": player.team_id,
            "game_id": live.game_id,
            "champion_name": live.champion_name,
            "queue_id": live.queue_id,
            "ranked": live.queue_id in (420, 440),
            "duration_s": live.elapsed_seconds(),
        },
    )


def _player_or_404(session: Session, player_id: int) -> Player:
    player = session.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PLAYER_NOT_FOUND_DETAIL)
    return player


def _team_or_404(session: Session, team_id: int) -> Team:
    team = session.get(Team, team_id)
    if team is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=TEAM_NOT_FOUND_DETAIL)
    return team


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


# ---------------------------------------------------------------------------
# Session admin
# ---------------------------------------------------------------------------


@router.post("/login")
async def login() -> dict[str, Any]:
    """Le mot de passe a déjà été vérifié par `require_admin`."""
    return {"ok": True}


# ---------------------------------------------------------------------------
# Challenge
# ---------------------------------------------------------------------------


@router.post("/draw")
async def draw(session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        result = perform_draw(session)
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    to_dict = getattr(result, "to_dict", None)
    payload = to_dict() if callable(to_dict) else asdict(result)
    # Annonce des duos sur Discord (optionnel, jamais bloquant)
    try:
        players_by_id = {p.id: p for p in session.exec(select(Player)).all()}
        await notifications.send_discord(notifications.format_draw_done(payload["teams"], players_by_id))
    except Exception:  # noqa: BLE001
        log.warning("Annonce Discord du tirage impossible", exc_info=True)
    return payload


@router.post("/challenge/start")
async def start_challenge(
    request: Request,
    body: StartIn | None = None,
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    if challenge.status == ChallengeStatus.RUNNING:
        raise _bad_request("Le challenge est déjà en cours.")
    has_teams = session.exec(select(Team.id).limit(1)).first() is not None
    if challenge.status == ChallengeStatus.REGISTRATION or not has_teams:
        raise _bad_request(DRAW_FIRST_DETAIL)

    start_at = parse_datetime(body.start_at if body is not None else None, "start_at") or utcnow()
    challenge.status = ChallengeStatus.RUNNING
    challenge.start_at = start_at
    challenge.end_at = None
    session.add(challenge)
    session.commit()
    session.refresh(challenge)
    bus.publish("challenge_started", {"challenge": challenge_to_dict(challenge)})

    # Premier cycle immédiat (snapshots de référence) ; les erreurs ne bloquent pas le démarrage
    poll_report: dict[str, Any] | None = None
    poll_errors: list[str] = []
    poller = getattr(request.app.state, "poller", None)
    if poller is not None:
        try:
            # Avec un `start_at` dans le passé, le premier cycle peut rapatrier beaucoup de parties
            # (limites Riot → ~2 min) : on n'attend pas plus de START_POLL_TIMEOUT_S, le cycle
            # continue en arrière-plan et le front le voit via l'événement SSE `poll_done`.
            report = await asyncio.wait_for(asyncio.shield(poller.poll_once()), timeout=START_POLL_TIMEOUT_S)
            poll_report = report.to_dict()
            poll_errors = list(report.errors)
        except TimeoutError:
            log.info("Cycle de démarrage toujours en cours : il se poursuit en arrière-plan")
        except Exception as exc:  # noqa: BLE001
            log.exception("Échec du cycle de démarrage")
            poll_errors = [type(exc).__name__]

    # Discord (optionnel) : jamais bloquant
    try:
        formatter = getattr(notifications, "format_challenge_started", None)
        content = formatter(challenge) if callable(formatter) else f"🚀 **{challenge.name}** : le challenge démarre !"
        await notifications.send_discord(content)
    except Exception:  # noqa: BLE001
        log.warning("Notification Discord de démarrage impossible", exc_info=True)

    session.refresh(challenge)
    return {"challenge": challenge_to_dict(challenge), "poll": poll_report, "poll_errors": poll_errors}


@router.post("/challenge/finish")
async def finish_challenge(
    request: Request,
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    if challenge.status != ChallengeStatus.RUNNING:
        raise _bad_request("Le challenge n'est pas en cours.")
    # Dernier cycle AVANT de figer la fin : les parties et LP du dernier intervalle comptent
    poller = getattr(request.app.state, "poller", None)
    if poller is not None:
        try:
            await poller.poll_once()
        except Exception:  # noqa: BLE001
            log.exception("Échec du cycle de clôture")
    session.expire_all()
    challenge = get_challenge(session)
    challenge.status = ChallengeStatus.FINISHED
    challenge.end_at = utcnow()
    session.add(challenge)
    session.commit()
    session.refresh(challenge)
    payload = challenge_to_dict(challenge)
    bus.publish("challenge_finished", {"challenge": payload})
    return {"challenge": payload}


@router.post("/challenge/reset")
async def reset_challenge(
    body: ResetIn | None = None,
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    keep_players = body.keep_players if body is not None else True
    # Ordre respectant les clés étrangères : participations → parties → snapshots → duos (→ joueurs)
    session.execute(delete(MatchParticipant))
    session.execute(delete(Match))
    session.execute(delete(RankSnapshot))
    for player in session.exec(select(Player)).all():
        player.team_id = None
        session.add(player)
    session.flush()
    session.execute(delete(Team))
    if not keep_players:
        session.execute(delete(Player))
    challenge.status = ChallengeStatus.REGISTRATION
    challenge.start_at = None
    challenge.end_at = None
    session.add(challenge)
    session.commit()
    session.refresh(challenge)
    state.live_games.clear()
    payload = challenge_to_dict(challenge)
    bus.publish("challenge_reset", {"challenge": payload, "keep_players": keep_players})
    return {"challenge": payload, "keep_players": keep_players}


@router.patch("/challenge")
async def patch_challenge(
    body: ChallengePatch,
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        name = body.name.strip()
        if not name:
            raise _bad_request("Le nom du challenge ne peut pas être vide.")
        challenge.name = name
    if "games_per_day" in fields and body.games_per_day is not None:
        if body.games_per_day < 1:
            raise _bad_request("Le nombre de parties par jour doit être au moins 1.")
        challenge.games_per_day = body.games_per_day
    if "start_at" in fields:
        challenge.start_at = parse_datetime(body.start_at, "start_at")
    if "end_at" in fields:
        challenge.end_at = parse_datetime(body.end_at, "end_at")
    if "track_flex" in fields and body.track_flex is not None:
        challenge.track_flex = body.track_flex
    session.add(challenge)
    session.commit()
    session.refresh(challenge)
    return {"challenge": challenge_to_dict(challenge)}


# ---------------------------------------------------------------------------
# Duos et joueurs
# ---------------------------------------------------------------------------


@router.patch("/teams/{team_id}")
async def patch_team(
    body: TeamPatch, team_id: EntityId, session: Session = Depends(get_session)
) -> dict[str, Any]:
    team = _team_or_404(session, team_id)
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        name = body.name.strip()
        if not name:
            raise _bad_request("Le nom du duo ne peut pas être vide.")
        team.name = name
    if "color" in fields and body.color is not None:
        color = body.color.strip().lower()
        if not HEX_COLOR_RE.match(color):
            raise _bad_request("Couleur invalide (format attendu : #rrggbb).")
        team.color = color
    if "window_start" in fields:
        team.window_start = parse_datetime(body.window_start, "window_start")
    if "window_end" in fields:
        team.window_end = parse_datetime(body.window_end, "window_end")
    session.add(team)
    session.commit()
    session.refresh(team)
    return {"team": team_public(team, _team_player_ids(session, team_id))}


@router.patch("/players/{player_id}")
async def patch_player(
    body: PlayerPatch, player_id: EntityId, session: Session = Depends(get_session)
) -> dict[str, Any]:
    player = _player_or_404(session, player_id)
    fields = body.model_fields_set
    if "display_name" in fields and body.display_name is not None:
        name = body.display_name.strip()
        if not name:
            raise _bad_request("Le pseudo ne peut pas être vide.")
        clash = session.exec(
            select(Player.id).where(func.lower(Player.display_name) == name.lower(), col(Player.id) != player_id)
        ).first()
        if clash is not None:
            raise _bad_request("Ce pseudo est déjà pris.")
        player.display_name = name
    if "active" in fields and body.active is not None:
        player.active = body.active
        if not body.active:
            _drop_live_game(player)
    if "team_id" in fields:
        if body.team_id is None:
            player.team_id = None
        else:
            player.team_id = _team_or_404(session, body.team_id).id
    session.add(player)
    session.commit()
    session.refresh(player)
    return {"player": player_public(player, _last_solo_snapshot(session, player_id))}


@router.delete("/players/{player_id}")
async def delete_player(player_id: EntityId, session: Session = Depends(get_session)) -> dict[str, Any]:
    player = _player_or_404(session, player_id)
    session.execute(delete(MatchParticipant).where(col(MatchParticipant.player_id) == player_id))
    session.execute(delete(RankSnapshot).where(col(RankSnapshot.player_id) == player_id))
    # Parties qui ne concernent plus aucun joueur du challenge
    session.execute(
        delete(Match).where(~col(Match.match_id).in_(select(MatchParticipant.match_id)))
    )
    _drop_live_game(player)
    session.delete(player)
    session.commit()
    return {"ok": True, "deleted_id": player_id}


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------


@router.post("/refresh")
async def refresh(request: Request) -> dict[str, Any]:
    poller = getattr(request.app.state, "poller", None)
    if poller is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Poller indisponible.")
    report = await poller.poll_once()
    return report.to_dict()


@router.post("/reload-settings")
async def reload_app_settings(request: Request) -> dict[str, Any]:
    """Relit `.env` (nouvelle clé Riot, mode démo…) et remplace le client Riot."""
    before = get_settings()
    settings = reload_settings()
    old_api = getattr(request.app.state, "riot_api", None)
    # Le client Riot n'est remplacé que si le mode change (démo ↔ réel) : en démo, le
    # recréer repartirait d'un monde simulé neuf (rangs initiaux, LP nets remis à zéro) ;
    # le client réel relit la clé à chaque requête, il n'a pas besoin d'être recréé.
    swapped = old_api is None or before.demo_mode != settings.demo_mode
    if swapped:
        reset_api()
        api = get_api()
        request.app.state.riot_api = api
        poller = getattr(request.app.state, "poller", None)
        if poller is not None:
            poller.api = api
        if old_api is not None:
            try:
                await old_api.aclose()
            except Exception:  # noqa: BLE001
                log.warning("Fermeture de l'ancien client Riot impossible", exc_info=True)
    return {"demo_mode": settings.demo_mode, "has_api_key": settings.has_api_key, "client_replaced": swapped}


@router.post("/test-notification")
async def test_notification() -> dict[str, Any]:
    try:
        sent = await notifications.send_discord(TEST_NOTIFICATION_CONTENT)
    except Exception:  # noqa: BLE001
        log.warning("Notification Discord de test impossible", exc_info=True)
        sent = False
    return {"sent": bool(sent)}
