"""Routes JSON publiques (`/api/...`) et `/health`.

Les formes JSON sont construites par `app.api.serializers` (source unique de vérité)
et les calculs par `app.api.leaderboard` → `app.services.stats`. Les routes de
lecture sont synchrones (threadpool) ; celles qui appellent l'API Riot ou publient
des événements sont asynchrones (le bus n'est pas thread-safe).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.api.deps import check_admin_password, get_challenge
from app.api.leaderboard import SORT_KEYS, build_leaderboard, compute_single_player_stats, team_window
from app.api.serializers import (
    challenge_to_dict,
    enum_value,
    iso,
    lp_point,
    match_row,
    player_public,
    snapshot_row,
    team_public,
)
from app.config import get_settings
from app.db.models import (
    Challenge,
    ChallengeStatus,
    MatchParticipant,
    Player,
    Queue,
    RankSnapshot,
    Team,
    game_end_of,
    utcnow,
)
from app.db.session import as_utc, get_session, session_scope
from app.events import bus
from app.riot import ddragon, get_api
from app.riot.base import RiotAPI
from app.services.registration import link_player, parse_riot_id, register_player
from app.state import state

log = logging.getLogger("pekin.api")

# `/health` n'est pas sous `/api` : on préfixe donc chaque chemin à la main.
router = APIRouter()

PING_INTERVAL_S = 20
FEED_DEFAULT_LIMIT = 30
FEED_MAX_LIMIT = 100
PLAYER_MATCHES_LIMIT = 50
MAX_DEMO_PLAYERS = 8
DEFAULT_SERIES_COLOR = "#9ca3af"
REGISTRATION_OPEN_STATUSES = (ChallengeStatus.REGISTRATION, ChallengeStatus.DRAWN)
REGISTRATION_CLOSED_DETAIL = "Les inscriptions sont closes."
PLAYER_NOT_FOUND_DETAIL = "Joueur introuvable."
# Flux SSE simultanés (un par onglet ouvert) ; au-delà → 503 le temps que ça se libère
MAX_SSE_SUBSCRIBERS = 100
# Garde-fou anti-spam sur l'inscription / la liaison : N requêtes par adresse et par fenêtre
WRITE_RATE_LIMIT = 40
WRITE_RATE_WINDOW_S = 600
RELINK_LOCKED_DETAIL = "Le challenge a démarré : seul l'organisateur peut changer un compte déjà lié."
# Identifiants : SQLite n'accepte pas d'entiers > 2^63 et un id démesuré n'existe jamais
PlayerId = Annotated[int, Path(ge=1, le=2**31 - 1)]


# ---------------------------------------------------------------------------
# Corps de requêtes
# ---------------------------------------------------------------------------


class RegisterIn(BaseModel):
    display_name: str
    riot_id: str | None = None


class LinkIn(BaseModel):
    riot_id: str


# ---------------------------------------------------------------------------
# Helpers de chargement
# ---------------------------------------------------------------------------


def _riot_api(request: Request) -> RiotAPI:
    """Client Riot de l'application (injecté par le lifespan) sinon le singleton."""
    api = getattr(request.app.state, "riot_api", None)
    return api if api is not None else get_api()


def _all_players(session: Session) -> list[Player]:
    return list(session.exec(select(Player).order_by(col(Player.id))).all())


def _all_teams(session: Session) -> list[Team]:
    return list(session.exec(select(Team).order_by(col(Team.slot), col(Team.id))).all())


_write_hits: dict[str, list[float]] = defaultdict(list)


def _check_write_rate(request: Request) -> None:
    """Limite simple en mémoire des écritures publiques (inscription, liaison) par adresse IP."""
    client_ip = request.client.host if request.client else "?"
    now = time.monotonic()
    hits = [t for t in _write_hits[client_ip] if now - t < WRITE_RATE_WINDOW_S]
    if len(hits) >= WRITE_RATE_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de tentatives, réessaie dans quelques minutes.",
        )
    hits.append(now)
    _write_hits[client_ip] = hits


def _player_or_404(session: Session, player_id: int) -> Player:
    player = session.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=PLAYER_NOT_FOUND_DETAIL)
    return player


def _solo_snapshots(session: Session, player_ids: list[int]) -> dict[int, list[RankSnapshot]]:
    """Snapshots SOLO par joueur, triés par date croissante."""
    grouped: dict[int, list[RankSnapshot]] = defaultdict(list)
    if not player_ids:
        return grouped
    rows = session.exec(
        select(RankSnapshot)
        .where(col(RankSnapshot.player_id).in_(player_ids), col(RankSnapshot.queue) == Queue.SOLO)
        .order_by(col(RankSnapshot.captured_at), col(RankSnapshot.id))
    ).all()
    for snapshot in rows:
        grouped[snapshot.player_id].append(snapshot)
    return grouped


def _last_solo_snapshots(session: Session, player_ids: list[int]) -> dict[int, RankSnapshot]:
    return {pid: snaps[-1] for pid, snaps in _solo_snapshots(session, player_ids).items() if snaps}


def _players_public(session: Session, players: list[Player]) -> list[dict[str, Any]]:
    last = _last_solo_snapshots(session, [p.id for p in players if p.id is not None])
    return [player_public(p, last.get(p.id)) for p in players]


def _player_public_fresh(session: Session, player: Player) -> dict[str, Any]:
    assert player.id is not None
    return player_public(player, _last_solo_snapshots(session, [player.id]).get(player.id))


def _teams_public(teams: list[Team], players: list[Player]) -> list[dict[str, Any]]:
    return [
        team_public(team, [p.id for p in players if p.team_id == team.id and p.id is not None]) for team in teams
    ]


def _teams_by_id(session: Session, team_ids: set[int]) -> dict[int, Team]:
    if not team_ids:
        return {}
    teams = session.exec(select(Team).where(col(Team.id).in_(list(team_ids)))).all()
    return {team.id: team for team in teams if team.id is not None}


def _last_poll_dict() -> dict[str, Any] | None:
    return state.last_poll.to_dict() if state.last_poll is not None else None


def _ensure_registration_open(challenge: Challenge) -> None:
    if challenge.status not in REGISTRATION_OPEN_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=REGISTRATION_CLOSED_DETAIL)


def _window_points(
    snapshots: list[RankSnapshot], start: datetime | None, end: datetime | None
) -> list[dict[str, Any]]:
    """Points de courbe : le snapshot de référence (dernier ≤ début) puis ceux de la fenêtre."""
    solo = [s for s in snapshots if s.queue == Queue.SOLO]
    if start is None:
        baseline: RankSnapshot | None = None
        inside = solo
    else:
        before = [s for s in solo if as_utc(s.captured_at) <= start]  # type: ignore[operator]
        baseline = before[-1] if before else None
        inside = [s for s in solo if as_utc(s.captured_at) > start]  # type: ignore[operator]
    if end is not None:
        inside = [s for s in inside if as_utc(s.captured_at) <= end]  # type: ignore[operator]
    points = ([baseline] if baseline is not None else []) + inside
    return [lp_point(s) for s in points]


def _live_items(session: Session, now: datetime | None = None) -> list[dict[str, Any]]:
    """Parties en cours (état mémoire du poller) enrichies des joueurs et duos."""
    now = now or utcnow()
    live = sorted(state.live_games.values(), key=lambda game: as_utc(game.detected_at) or now)
    if not live:
        return []
    player_ids = [game.player_id for game in live]
    players = {
        p.id: p for p in session.exec(select(Player).where(col(Player.id).in_(player_ids))).all() if p.id is not None
    }
    teams = _teams_by_id(session, {p.team_id for p in players.values() if p.team_id is not None})
    items: list[dict[str, Any]] = []
    for game in live:
        player = players.get(game.player_id)
        if player is None:
            continue
        team = teams.get(player.team_id) if player.team_id is not None else None
        game_start = as_utc(game.game_start)
        if game_start is None or game_start.timestamp() <= 0:
            game_start = as_utc(game.detected_at)
        items.append(
            {
                "player_id": player.id,
                "display_name": player.display_name,
                "team_id": team.id if team is not None else None,
                "team_name": team.name if team is not None else None,
                "team_color": team.color if team is not None else None,
                "champion_name": game.champion_name,
                "champion_icon_url": ddragon.champion_icon_url(ddragon.CURRENT_VERSION, game.champion_name),
                "champion_loading_url": ddragon.champion_loading_url(game.champion_name),
                "champion_splash_url": ddragon.champion_splash_url(game.champion_name),
                "game_start": iso(game_start),
                "elapsed_s": game.elapsed_seconds(now),
                "queue_id": game.queue_id,
                "game_mode": game.game_mode,
            }
        )
    return items


# ---------------------------------------------------------------------------
# Lecture
# ---------------------------------------------------------------------------


@router.get("/health")
def health(challenge: Challenge = Depends(get_challenge)) -> dict[str, Any]:
    return {
        "status": "ok",
        "demo_mode": get_settings().demo_mode,
        "challenge_status": enum_value(challenge.status),
        "last_poll": _last_poll_dict(),
        "live_count": len(state.live_games),
    }


@router.get("/api/state")
def get_state(
    session: Session = Depends(get_session), challenge: Challenge = Depends(get_challenge)
) -> dict[str, Any]:
    players = _all_players(session)
    teams = _all_teams(session)
    return {
        "challenge": challenge_to_dict(challenge),
        "players": _players_public(session, players),
        "teams": _teams_public(teams, players),
        "demo_mode": get_settings().demo_mode,
        "games_per_day": challenge.games_per_day,
        "live_count": len(state.live_games),
        "last_poll": _last_poll_dict(),
        "base_url": get_settings().base_url,
    }


@router.get("/api/leaderboard")
def get_leaderboard(
    sort: str = "lp_net",
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    if sort not in SORT_KEYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tri inconnu « {sort} » (valeurs possibles : {', '.join(SORT_KEYS)}).",
        )
    now = utcnow()
    teams, players = build_leaderboard(session, challenge, now=now, sort=sort)
    return {
        "challenge": challenge_to_dict(challenge),
        "teams": [team.to_dict() for team in teams],
        "players": [player.to_dict() for player in players],
        "generated_at": now.isoformat(),
    }


@router.get("/api/duos")
def get_duos(
    session: Session = Depends(get_session), challenge: Challenge = Depends(get_challenge)
) -> dict[str, Any]:
    """Page « Duos » : stats complètes par duo (classées) + joueurs actifs sans duo."""
    now = utcnow()
    teams, _players = build_leaderboard(session, challenge, now=now)
    unassigned = [p for p in _all_players(session) if p.active and p.team_id is None]
    return {
        "challenge": challenge_to_dict(challenge),
        "teams": [team.to_dict() for team in teams],
        "unassigned_players": _players_public(session, unassigned),
        "games_per_day": challenge.games_per_day,
        "generated_at": now.isoformat(),
    }


@router.get("/api/players/{player_id}")
def get_player(
    player_id: PlayerId,
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    player = _player_or_404(session, player_id)
    team = session.get(Team, player.team_id) if player.team_id is not None else None
    stats = compute_single_player_stats(session, challenge, player, team)
    participants = session.exec(
        select(MatchParticipant)
        .where(col(MatchParticipant.player_id) == player_id)
        .order_by(col(MatchParticipant.game_start).desc(), col(MatchParticipant.id).desc())
        .limit(PLAYER_MATCHES_LIMIT)
    ).all()
    snapshots = _solo_snapshots(session, [player_id]).get(player_id, [])
    team_dict = None
    if team is not None:
        mates = session.exec(select(Player.id).where(col(Player.team_id) == team.id).order_by(col(Player.id))).all()
        team_dict = team_public(team, [pid for pid in mates if pid is not None])
    return {
        "player": player_public(player, snapshots[-1] if snapshots else None),
        "team": team_dict,
        "stats": stats.to_dict(),
        "matches": [match_row(participant, player) for participant in participants],
        "snapshots": [snapshot_row(snapshot) for snapshot in snapshots],
    }


@router.get("/api/players/{player_id}/lp-history")
def get_player_lp_history(
    player_id: PlayerId,
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    player = _player_or_404(session, player_id)
    team = session.get(Team, player.team_id) if player.team_id is not None else None
    start, end = team_window(challenge, team)
    snapshots = _solo_snapshots(session, [player_id]).get(player_id, [])
    return {"player_id": player_id, "points": _window_points(snapshots, start, end)}


@router.get("/api/lp-history")
def get_lp_history(
    session: Session = Depends(get_session), challenge: Challenge = Depends(get_challenge)
) -> dict[str, Any]:
    """Une courbe par joueur actif et lié (fenêtre de son duo, point de référence inclus)."""
    players = [p for p in _all_players(session) if p.active and p.is_linked and p.id is not None]
    teams = _teams_by_id(session, {p.team_id for p in players if p.team_id is not None})
    snapshots_by = _solo_snapshots(session, [p.id for p in players if p.id is not None])
    series: list[dict[str, Any]] = []
    for player in players:
        assert player.id is not None
        snapshots = snapshots_by.get(player.id, [])
        if not snapshots:
            continue
        team = teams.get(player.team_id) if player.team_id is not None else None
        start, end = team_window(challenge, team)
        points = _window_points(snapshots, start, end)
        if not points:
            continue
        series.append(
            {
                "player_id": player.id,
                "display_name": player.display_name,
                "team_id": team.id if team is not None else None,
                "color": team.color if team is not None else DEFAULT_SERIES_COLOR,
                "points": points,
            }
        )
    return {"series": series}


@router.get("/api/feed")
def get_feed(limit: int = FEED_DEFAULT_LIMIT, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Dernières parties (toutes queues, hors remakes), la plus récente d'abord."""
    limit = max(1, min(FEED_MAX_LIMIT, limit))
    participants = session.exec(
        select(MatchParticipant)
        .where(col(MatchParticipant.is_remake).is_(False))
        .order_by(col(MatchParticipant.game_start).desc(), col(MatchParticipant.id).desc())
        .limit(limit)
    ).all()
    players = {p.id: p for p in _all_players(session) if p.id is not None}
    teams = _teams_by_id(session, {p.team_id for p in players.values() if p.team_id is not None})
    now = utcnow()
    items: list[dict[str, Any]] = []
    for participant in participants:
        player = players.get(participant.player_id)
        if player is None:
            continue
        team = teams.get(player.team_id) if player.team_id is not None else None
        row = match_row(participant, player)
        ended_at = game_end_of(participant)
        row.update(
            {
                "display_name": player.display_name,
                "team_id": team.id if team is not None else None,
                "team_name": team.name if team is not None else None,
                "team_color": team.color if team is not None else None,
                "ago_s": max(0, int((now - ended_at).total_seconds())),
            }
        )
        items.append(row)
    return {"items": items}


@router.get("/api/live")
def get_live(session: Session = Depends(get_session)) -> dict[str, Any]:
    return {"live": _live_items(session)}


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


def _sse_message(event_id: int | None, event_type: str, data: Any) -> str:
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    return "\n".join(lines) + "\n\n"


async def _pump_events(since_id: int | None, queue: asyncio.Queue[dict[str, Any]]) -> None:
    """Recopie le flux du bus dans une file locale (annulable sans casser l'abonnement)."""
    async for event in bus.subscribe(since_id):
        await queue.put(event)


async def _event_stream(since_id: int | None, hello: dict[str, Any], max_events: int | None):
    """Générateur SSE : `hello` immédiat, événements du bus, `: ping` toutes les 20 s si rien."""
    sent = 1
    yield _sse_message(None, "hello", hello)
    if max_events is not None and sent >= max_events:
        return
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    # On ne peut pas annuler `__anext__()` du générateur du bus (ça le fermerait) :
    # une tâche de pompage + `wait_for` sur la file locale donnent le même résultat.
    pump = asyncio.create_task(_pump_events(since_id, queue), name="sse-pump")
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL_S)
            except TimeoutError:
                if pump.done():
                    break
                yield ": ping\n\n"
                continue
            yield _sse_message(event["id"], event["type"], event["data"])
            sent += 1
            if max_events is not None and sent >= max_events:
                break
    finally:
        pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await pump


@router.get("/api/events")
async def get_events(
    request: Request,
    since: int | None = None,
    max_events: int | None = Query(default=None, ge=1, description="Ferme le flux après N événements (tests, curl)."),
) -> StreamingResponse:
    # Pas de `Depends(get_session)` ici : une dépendance `yield` vivrait aussi longtemps
    # que le flux SSE et garderait une connexion du pool SQLite par onglet ouvert.
    if bus.subscriber_count >= MAX_SSE_SUBSCRIBERS:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trop de connexions temps réel ouvertes, réessaie dans un instant.",
        )
    since_id = since
    if since_id is None:
        header = request.headers.get("last-event-id")
        if header:
            with contextlib.suppress(ValueError):
                since_id = int(header)
    recent = bus.recent(limit=1)
    with session_scope() as session:
        challenge = get_challenge(session)
        hello = {
            "live": _live_items(session),
            "challenge_status": enum_value(challenge.status),
            "challenge": challenge_to_dict(challenge),
            "live_count": len(state.live_games),
            "last_event_id": recent[-1]["id"] if recent else 0,
        }
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        _event_stream(since_id, hello, max_events), media_type="text/event-stream", headers=headers
    )


# ---------------------------------------------------------------------------
# Écriture (inscription, liaison, démo)
# ---------------------------------------------------------------------------


@router.post("/api/players", status_code=status.HTTP_201_CREATED)
async def create_player(
    body: RegisterIn,
    request: Request,
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    _ensure_registration_open(challenge)
    _check_write_rate(request)
    display_name = body.display_name.strip()
    riot_id = (body.riot_id or "").strip() or None
    if not display_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le pseudo est obligatoire.")
    max_players = get_settings().max_players
    active_count = len(session.exec(select(Player.id).where(Player.active == True)).all())  # noqa: E712
    if active_count >= max_players:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Toutes les places sont prises ({max_players} joueurs).",
        )
    try:
        if riot_id is not None:
            parse_riot_id(riot_id)  # validation du format avant toute création
        player = await register_player(session, _riot_api(request), display_name=display_name, riot_id=riot_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"player": _player_public_fresh(session, player)}


@router.post("/api/players/{player_id}/link")
async def link_player_account(
    body: LinkIn,
    request: Request,
    player_id: PlayerId,
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
    x_admin_password: str | None = Header(default=None),
) -> dict[str, Any]:
    player = _player_or_404(session, player_id)
    _check_write_rate(request)
    # Un compte déjà lié ne change plus une fois le challenge démarré (sinon l'historique de rang
    # serait remplacé) — sauf pour l'organisateur. Lier un compte encore absent reste possible.
    if player.is_linked and challenge.status not in REGISTRATION_OPEN_STATUSES:
        if not check_admin_password(x_admin_password):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=RELINK_LOCKED_DETAIL)
    riot_id = body.riot_id.strip()
    try:
        parse_riot_id(riot_id)
        player = await link_player(session, _riot_api(request), player, riot_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"player": _player_public_fresh(session, player)}


@router.post("/api/demo/fill")
async def demo_fill(
    request: Request,
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    """Mode démo : inscrit les joueurs de `seed_names` manquants jusqu'à 8 joueurs actifs."""
    if not get_settings().demo_mode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mode démo désactivé.")
    _ensure_registration_open(challenge)
    api = _riot_api(request)
    seeds = list(getattr(api, "seed_names", None) or [])
    if not seeds:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Aucun joueur démo disponible.")
    players = _all_players(session)
    taken_names = {p.display_name.strip().casefold() for p in players}
    taken_riot_ids = {p.riot_id.casefold() for p in players if p.riot_id}
    active_count = sum(1 for p in players if p.active)
    for display_name, riot_id in seeds:
        if active_count >= MAX_DEMO_PLAYERS:
            break
        if display_name.casefold() in taken_names or (riot_id and riot_id.casefold() in taken_riot_ids):
            continue
        try:
            await register_player(session, api, display_name=display_name, riot_id=riot_id)
        except ValueError as exc:
            log.warning("Joueur démo %s ignoré : %s", display_name, exc)
            continue
        taken_names.add(display_name.casefold())
        if riot_id:
            taken_riot_ids.add(riot_id.casefold())
        active_count += 1
    session.expire_all()
    players = _all_players(session)
    return {"players": _players_public(session, players)}
