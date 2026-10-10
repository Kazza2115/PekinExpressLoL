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
from app.db.models import Challenge, ChallengeStatus, Joker, Match, MatchParticipant, Player, Queue, RankSnapshot, Team, utcnow
from app.db.session import as_utc, get_session
from app.services.bootstrap import apply_default_schedule, parse_fr_datetime
from app.events import bus
from app.riot import get_api, reset_api
from app.services import gifs, notifications
from app.services.announce import announce_start_if_due
from app.services.draw import perform_draw, team_identity
from app.state import state

log = logging.getLogger("pekin.admin")

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])

HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
NO_TEAMS_DETAIL = "Aucun duo : compose-les dans Admin → Duos."
TEAMS_LOCKED_DETAIL = "Les duos ne peuvent plus changer pendant le challenge."
TEAM_NOT_FOUND_DETAIL = "Duo introuvable."
PLAYER_NOT_FOUND_DETAIL = "Joueur introuvable."
# Taille d'un duo
TEAM_SIZE = 2


# ---------------------------------------------------------------------------
# Corps de requêtes
# ---------------------------------------------------------------------------


class StartIn(BaseModel):
    start_at: str | None = None  # ISO 8601 ; absent : début programmé, sinon maintenant
    end_at: str | None = None  # ISO 8601 ; absent : fin programmée ; null : pas de fin


class ResetIn(BaseModel):
    keep_players: bool = True


class ChallengePatch(BaseModel):
    name: str | None = Field(default=None, max_length=60)
    games_per_day: int | None = Field(default=None, ge=1, le=100)
    start_at: str | None = None  # "" ou null → efface
    end_at: str | None = None
    track_flex: bool | None = None
    jokers_per_team: int | None = Field(default=None, ge=0, le=10)
    joker_extra_games: int | None = Field(default=None, ge=0, le=20)


class TeamCreate(BaseModel):
    name: str | None = Field(default=None, max_length=40)  # défaut : palette (`team_identity(slot)`)
    color: str | None = None
    player_ids: list[int] = Field(default_factory=list)  # 0 à 2 joueurs (contrôlé en 400 FR)


class TeamPatch(BaseModel):
    name: str | None = Field(default=None, max_length=40)
    color: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    player_ids: list[int] | None = None  # remplace la composition (0 à 2 joueurs)


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
    french = parse_fr_datetime(raw, get_settings().tz)
    if french is not None:
        return french
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


def _parse_color(value: str) -> str:
    color = value.strip().lower()
    if not HEX_COLOR_RE.match(color):
        raise _bad_request("Couleur invalide (format attendu : #rrggbb).")
    return color


def _ensure_teams_editable(challenge: Challenge) -> None:
    """La composition des duos est figée une fois le challenge démarré : en cours (`running`) comme
    terminé (`finished`, le classement final ne doit plus bouger). « Réinitialiser » la rouvre."""
    if challenge.status in (ChallengeStatus.RUNNING, ChallengeStatus.FINISHED):
        raise _bad_request(TEAMS_LOCKED_DETAIL)


def _assign_players(session: Session, team: Team, player_ids: list[int]) -> None:
    """Remplace la composition du duo par `player_ids` (0 à 2 joueurs existants et actifs).

    Un joueur déjà dans un autre duo en est retiré ; les anciens membres absents de la
    liste sont désassignés. Ne commit pas.
    """
    unique_ids = list(dict.fromkeys(player_ids))
    if len(unique_ids) != len(player_ids):
        raise _bad_request("Un joueur ne peut pas être deux fois dans le même duo.")
    if len(unique_ids) > TEAM_SIZE:
        raise _bad_request(f"Un duo compte au plus {TEAM_SIZE} joueurs.")
    players: list[Player] = []
    for player_id in unique_ids:
        player = session.get(Player, player_id)
        if player is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Joueur {player_id} introuvable."
            )
        if not player.active:
            raise _bad_request(f"{player.display_name} est désactivé : réactive-le avant de le mettre dans un duo.")
        players.append(player)
    for member in session.exec(select(Player).where(col(Player.team_id) == team.id)).all():
        if member.id not in unique_ids:
            member.team_id = None
            session.add(member)
    for player in players:
        player.team_id = team.id
        session.add(player)


def _check_teams_ready(session: Session) -> list[str]:
    """Pré-conditions du démarrage : des duos de joueurs actifs et liés, personne sur le banc.

    Un duo d'un seul joueur est toléré (utile pour tester seul) : renvoyé en avertissement.
    """
    teams = session.exec(select(Team).order_by(col(Team.slot), col(Team.id))).all()
    if not teams:
        raise _bad_request(NO_TEAMS_DETAIL)
    active_players = session.exec(select(Player).where(col(Player.active).is_(True)).order_by(col(Player.id))).all()
    team_ids = {team.id for team in teams}
    warnings: list[str] = []
    for team in teams:
        members = [p for p in active_players if p.team_id == team.id]
        if len(members) == 0:
            raise _bad_request(f"Le duo {team.name} est vide.")
        if len(members) == 1:
            warnings.append(f"Le duo {team.name} n'a qu'un joueur ({members[0].display_name}).")
        if len(members) > TEAM_SIZE:
            raise _bad_request(f"Le duo {team.name} a {len(members)} joueurs ({TEAM_SIZE} attendus).")
        unlinked = [p.display_name for p in members if not p.is_linked]
        if unlinked:
            raise _bad_request(f"Le duo {team.name} a un compte non lié : {', '.join(unlinked)}.")
    benched = [p.display_name for p in active_players if p.is_linked and p.team_id not in team_ids]
    if benched:
        raise _bad_request("Joueurs sans duo : " + ", ".join(benched))
    return warnings


def _draw_payload(result: Any) -> dict[str, Any]:
    to_dict = getattr(result, "to_dict", None)
    return to_dict() if callable(to_dict) else asdict(result)


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


@router.post("/teams/auto")
@router.post("/draw")  # alias historique (roue de tirage)
async def draw(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Tirage aléatoire des duos (remplace tous les duos existants)."""
    try:
        result = perform_draw(session)
    except ValueError as exc:
        raise _bad_request(str(exc)) from exc
    payload = _draw_payload(result)
    # Annonce des duos sur Discord (optionnel, jamais bloquant)
    try:
        players_by_id = {p.id: p for p in session.exec(select(Player)).all()}
        await notifications.send_discord(
            notifications.headline(notifications.format_draw_done(payload["teams"], players_by_id)),
            embeds=[notifications.draw_embed(payload["teams"], players_by_id)],
        )
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
    # Accepté depuis registration / drawn / finished dès que les duos sont complets
    warnings = _check_teams_ready(session)
    settings = get_settings()
    if settings.tz_fallback:
        warnings.append(
            f"Fuseau horaire {settings.timezone} introuvable : installe le paquet tzdata "
            "(relance PekinExpress.bat). En attendant, les journées sont comptées en UTC."
        )

    # Dates programmées dans l'Admin (Début / Fin) avant le démarrage : on les garde. « Démarrer » la
    # veille avec un début à samedi 9h ne compte que les parties terminées à partir de samedi 9h.
    # Après un challenge terminé, les anciennes dates appartiennent au challenge précédent.
    planned = challenge.status in (ChallengeStatus.REGISTRATION, ChallengeStatus.DRAWN)
    planned_start = as_utc(challenge.start_at) if planned else None
    planned_end = as_utc(challenge.end_at) if planned else None
    fields = body.model_fields_set if body is not None else set()
    if "end_at" in fields:
        planned_end = as_utc(parse_datetime(body.end_at, "end_at"))  # type: ignore[union-attr]
    start_at = (
        as_utc(parse_datetime(body.start_at if body is not None else None, "start_at"))
        or planned_start
        or utcnow()
    )
    end_at = planned_end if planned_end is not None and planned_end > start_at else None
    if planned_end is not None and end_at is None:
        warnings.append("La date de fin enregistrée est avant le début : ignorée (fin au clic sur « Terminer »).")
    challenge.status = ChallengeStatus.RUNNING
    challenge.start_at = start_at
    challenge.end_at = end_at
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

    # Discord (optionnel) : jamais bloquant. Début déjà atteint : l'annonce « c'est parti » (GIF,
    # duos) part tout de suite ; début programmé : « tout est prêt », puis l'annonce à l'heure dite.
    try:
        if as_utc(challenge.start_at) is not None and as_utc(challenge.start_at) <= utcnow():  # type: ignore[operator]
            await announce_start_if_due(settings=settings)
        else:
            await notifications.send_discord(
                notifications.headline(notifications.format_challenge_started(challenge)),
                embeds=[notifications.challenge_started_embed(challenge)],
            )
    except Exception:  # noqa: BLE001
        log.warning("Notification Discord de démarrage impossible", exc_info=True)

    session.refresh(challenge)
    return {"challenge": challenge_to_dict(challenge), "poll": poll_report, "poll_errors": poll_errors, "warnings": warnings}


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
    # Une fin programmée déjà passée reste la fin (cliquer « Terminer » lundi à 10h ne fait pas
    # compter les parties de lundi matin) ; sinon, la fin est maintenant.
    now = utcnow()
    planned_end = as_utc(challenge.end_at)
    challenge.end_at = planned_end if planned_end is not None and planned_end < now else now
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
    session.execute(delete(Joker))
    session.execute(delete(Team))
    if not keep_players:
        session.execute(delete(Player))
    challenge.status = ChallengeStatus.REGISTRATION
    challenge.start_at = None
    challenge.end_at = None
    challenge.start_announced_at = None
    session.add(challenge)
    session.commit()
    session.refresh(challenge)
    state.live_games.clear()
    payload = challenge_to_dict(challenge)
    if apply_default_schedule(session):
        session.refresh(challenge)
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
        new_start = parse_datetime(body.start_at, "start_at")
        if new_start is None and challenge.status in (ChallengeStatus.RUNNING, ChallengeStatus.FINISHED):
            raise _bad_request("Impossible d'effacer le début d'un challenge démarré.")
        challenge.start_at = new_start
    reopened = False
    if "end_at" in fields:
        new_end = parse_datetime(body.end_at, "end_at")
        challenge.end_at = new_end
        # Fin repoussée dans le futur après une fin (automatique ou par erreur) : le challenge reprend
        if challenge.status == ChallengeStatus.FINISHED and new_end is not None and as_utc(new_end) > utcnow():
            challenge.status = ChallengeStatus.RUNNING
            reopened = True
    if "track_flex" in fields and body.track_flex is not None:
        challenge.track_flex = body.track_flex
    if "jokers_per_team" in fields and body.jokers_per_team is not None:
        challenge.jokers_per_team = body.jokers_per_team
    if "joker_extra_games" in fields and body.joker_extra_games is not None:
        challenge.joker_extra_games = body.joker_extra_games
    session.add(challenge)
    session.commit()
    session.refresh(challenge)
    if reopened:
        bus.publish("challenge_started", {"challenge": challenge_to_dict(challenge), "reopened": True})
    return {"challenge": challenge_to_dict(challenge), "reopened": reopened}


# ---------------------------------------------------------------------------
# Duos et joueurs
# ---------------------------------------------------------------------------


@router.post("/teams", status_code=status.HTTP_201_CREATED)
async def create_team(
    body: TeamCreate | None = None,
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    """Crée un duo à la main (nom / couleur de la palette par défaut, 0 à 2 joueurs)."""
    _ensure_teams_editable(challenge)
    body = body if body is not None else TeamCreate()
    max_slot = session.exec(select(func.max(Team.slot))).one()
    slot = int(max_slot or 0) + 1
    default_name, default_color = team_identity(slot)
    name = (body.name or "").strip() or default_name
    color = _parse_color(body.color) if body.color and body.color.strip() else default_color
    team = Team(name=name, color=color, slot=slot)
    session.add(team)
    session.flush()  # récupère team.id
    _assign_players(session, team, list(body.player_ids))
    session.commit()
    session.refresh(team)
    assert team.id is not None
    payload = team_public(team, _team_player_ids(session, team.id))
    bus.publish("teams_changed", {"action": "created", "team": payload})
    return {"team": payload}


@router.patch("/teams/{team_id}")
async def patch_team(
    body: TeamPatch,
    team_id: EntityId,
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    team = _team_or_404(session, team_id)
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        name = body.name.strip()
        if not name:
            raise _bad_request("Le nom du duo ne peut pas être vide.")
        team.name = name
    if "color" in fields and body.color is not None:
        team.color = _parse_color(body.color)
    if "window_start" in fields:
        team.window_start = parse_datetime(body.window_start, "window_start")
    if "window_end" in fields:
        team.window_end = parse_datetime(body.window_end, "window_end")
    if "player_ids" in fields and body.player_ids is not None:
        # Nom, couleur et fenêtre restent modifiables en cours de challenge ; pas la composition
        _ensure_teams_editable(challenge)
        _assign_players(session, team, list(body.player_ids))
    session.add(team)
    session.commit()
    session.refresh(team)
    payload = team_public(team, _team_player_ids(session, team_id))
    bus.publish("teams_changed", {"action": "updated", "team": payload})
    return {"team": payload}


@router.delete("/teams/{team_id}")
async def delete_team(
    team_id: EntityId,
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    """Supprime un duo ; ses joueurs sont désassignés (jamais supprimés)."""
    _ensure_teams_editable(challenge)
    team = _team_or_404(session, team_id)
    released: list[int] = []
    for member in session.exec(select(Player).where(col(Player.team_id) == team_id)).all():
        member.team_id = None
        session.add(member)
        if member.id is not None:
            released.append(member.id)
    session.flush()
    for joker in session.exec(select(Joker).where(Joker.team_id == team_id)).all():
        session.delete(joker)
    session.delete(team)
    session.commit()
    bus.publish("teams_changed", {"action": "deleted", "team_id": team_id, "player_ids": released})
    return {"ok": True, "deleted_id": team_id, "player_ids": released}


@router.delete("/jokers/{joker_id}")
async def cancel_joker(joker_id: EntityId, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Annule un joker (activé par erreur ou par quelqu'un d'autre) : le duo le récupère."""
    joker = session.get(Joker, joker_id)
    if joker is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Joker introuvable.")
    team_id = joker.team_id
    session.delete(joker)
    session.commit()
    bus.publish("joker_cancelled", {"joker_id": joker_id, "team_id": team_id})
    return {"ok": True, "joker_id": joker_id, "team_id": team_id}


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
    released_from: int | None = None
    if "active" in fields and body.active is not None:
        player.active = body.active
        if not body.active:
            _drop_live_game(player)
            if player.team_id is not None:
                # « Désactiver (exclu du suivi et des duos) » : le duo ne garde pas un membre fantôme.
                # Sinon l'éditeur Admin (actifs seulement) affiche « — Personne — » sous une légende
                # « Alpha & Bravo » et « Enregistrer » sans rien toucher éjecte le joueur en silence.
                released_from = player.team_id
                player.team_id = None
    if "team_id" in fields:  # traité après `active` : un `team_id` explicite garde la priorité
        if body.team_id is None:
            player.team_id = None
        else:
            player.team_id = _team_or_404(session, body.team_id).id
    session.add(player)
    session.commit()
    session.refresh(player)
    if released_from is not None:
        # Admin et /duos rechargent sur `teams_changed`
        bus.publish("teams_changed", {"action": "updated", "team_id": released_from, "player_ids": [player.id]})
    return {"player": player_public(player, _last_solo_snapshot(session, player_id))}


@router.delete("/players/{player_id}")
async def delete_player(player_id: EntityId, session: Session = Depends(get_session)) -> dict[str, Any]:
    player = _player_or_404(session, player_id)
    _delete_player_rows(session, player)
    session.commit()
    return {"ok": True, "deleted_id": player_id}


def _delete_player_rows(session: Session, player: Player) -> None:
    """Supprime un joueur, ses photos de rang, ses participations et les parties orphelines (sans commit)."""
    session.execute(delete(MatchParticipant).where(col(MatchParticipant.player_id) == player.id))
    session.execute(delete(RankSnapshot).where(col(RankSnapshot.player_id) == player.id))
    for joker in session.exec(select(Joker).where(Joker.player_id == player.id)).all():
        joker.player_id = None  # le joker reste au duo, sans auteur
        session.add(joker)
    # Parties qui ne concernent plus aucun joueur du challenge
    session.execute(delete(Match).where(~col(Match.match_id).in_(select(MatchParticipant.match_id))))
    _drop_live_game(player)
    session.delete(player)


DEMO_REMOVED_HINT_RUNNING = (
    "Le challenge est toujours en cours : les duos sont figés. Clique « Réinitialiser » "
    "(en gardant les joueurs) pour recomposer les duos avec les vrais joueurs."
)
DEMO_REMOVED_HINT_FINISHED = (
    "Le challenge est terminé : les duos sont figés. Clique « Réinitialiser » "
    "(en gardant les joueurs) pour recomposer les duos."
)


@router.delete("/players/demo/all")
async def delete_demo_players(
    session: Session = Depends(get_session),
    challenge: Challenge = Depends(get_challenge),
) -> dict[str, Any]:
    """Supprime les joueurs créés en mode démo (puuid `demo-…`) : inutiles et bloquants en mode réel.

    Avant le démarrage (`registration` / `drawn`), les duos vidés PAR cette suppression sont retirés
    aussi : un duo sans joueur bloque « Démarrer » (« Le duo X est vide. ») et s'affiche vide sur
    /duos. Un duo vide créé à la main (sans joueur démo) ou un duo gardant un vrai joueur est
    conservé. Pendant / après le challenge, les duos sont figés : rien d'autre n'est touché et la
    réponse porte un `hint` invitant à « Réinitialiser » (en gardant les joueurs).
    """
    players = [p for p in session.exec(select(Player)).all() if (p.puuid or "").startswith("demo-")]
    names = [p.display_name for p in players]
    deleted_ids = [p.id for p in players if p.id is not None]
    # Duos qui contenaient au moins un joueur démo : seuls ceux-là peuvent avoir été vidés ici
    touched = {p.team_id for p in players if p.team_id is not None}
    for player in players:
        _delete_player_rows(session, player)
    session.flush()  # clé étrangère player.team_id : les joueurs partent avant les duos

    removed_team_ids: list[int] = []
    hint: str | None = None
    if challenge.status in (ChallengeStatus.REGISTRATION, ChallengeStatus.DRAWN):
        for team_id in sorted(touched):
            team = session.get(Team, team_id)
            if team is not None and not _team_player_ids(session, team_id):
                for joker in session.exec(select(Joker).where(Joker.team_id == team_id)).all():
                    session.delete(joker)
                session.delete(team)
                removed_team_ids.append(team_id)
        session.flush()
        if challenge.status == ChallengeStatus.DRAWN and session.exec(select(Team.id)).first() is None:
            # Plus aucun duo : le tirage n'existe plus, on revient à la phase d'inscription
            challenge.status = ChallengeStatus.REGISTRATION
            session.add(challenge)
    elif touched and challenge.status == ChallengeStatus.RUNNING:
        hint = DEMO_REMOVED_HINT_RUNNING
    elif touched and challenge.status == ChallengeStatus.FINISHED:
        hint = DEMO_REMOVED_HINT_FINISHED
    session.commit()
    session.refresh(challenge)
    if players:
        # `teams_changed` : Admin et /duos rechargent ; pas `challenge_reset`, qui afficherait
        # partout « ♻️ Le challenge a été réinitialisé. » alors que le statut n'a pas changé
        bus.publish(
            "teams_changed",
            {"action": "demo_removed", "team_ids": removed_team_ids, "player_ids": deleted_ids},
        )
    return {
        "ok": True,
        "deleted": len(players),
        "names": names,
        "teams_removed": len(removed_team_ids),
        "status": challenge.status,
        "hint": hint,
    }


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


@router.post("/live-check")
async def live_check(request: Request) -> dict[str, Any]:
    """Diagnostic : interroge tout de suite Riot (Spectator) pour chaque joueur lié.

    Une partie trouvée et pas encore connue publie `live_start` (toast, notification, Discord) :
    c'est aussi un moyen de tester la chaîne de notification avec une vraie partie.
    """
    poller = getattr(request.app.state, "poller", None)
    if poller is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Poller indisponible.")
    result = await poller.poll_live_once(wait=True)
    settings = get_settings()
    result["live_poll_seconds"] = settings.live_poll_seconds
    result["poll_interval_seconds"] = settings.poll_interval_seconds
    result["demo_mode"] = settings.demo_mode
    # Fin de la clé seulement (page Admin) : à comparer avec developer.riotgames.com
    key = settings.riot_api_key.strip()
    result["key_hint"] = f"…{key[-4:]}" if len(key) >= 8 else None
    return result


@router.post("/reload-settings")
async def reload_app_settings(request: Request) -> dict[str, Any]:
    """Relit `.env` (nouvelle clé Riot, mode démo…) et remplace le client Riot."""
    before = get_settings()
    settings = reload_settings()
    gifs.reset_cache()  # nouvelle clé Klipy ou nouvelles catégories : on recherche à nouveau
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
    """Message de test : exemple de carte, GIF de victoire et de défaite, mention du rôle.

    Discord renvoie le message créé (`wait=true`) : on sait alors si la mention du rôle a
    vraiment été retenue (`role_recognized`), ce que le simple envoi ne dit pas.
    """
    settings = get_settings()
    role_id = settings.discord_role_id
    try:
        content, embeds = await notifications.build_test_message(settings=settings)
        result = await notifications.post_discord(content, embeds=embeds, wait=True, settings=settings)
    except Exception:  # noqa: BLE001
        log.warning("Notification Discord de test impossible", exc_info=True)
        result = notifications.DiscordSendResult(sent=False, error="erreur inattendue (voir le journal)")
    recognized = None
    if result.sent and role_id and result.mention_roles is not None:
        recognized = role_id in result.mention_roles
    return {
        "sent": bool(result.sent),
        "error": result.error,
        "role_mention": bool(role_id),
        "role_id": role_id or None,
        "role_id_invalid": settings.discord_role_id_invalid or None,
        "role_recognized": recognized,
        "klipy": bool(settings.klipy_api_key),
    }