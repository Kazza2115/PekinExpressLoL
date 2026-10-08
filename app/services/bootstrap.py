"""Amorçage au démarrage : ligne `Challenge` unique, joueurs de `players.yaml`, rattrapage des
détails de partie (colonnes ajoutées après coup, ré-extraites du JSON Match-V5 stocké)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import yaml
from sqlmodel import Session, col, select

from app.config import get_settings
from app.db.models import Challenge, Match, MatchParticipant, Player
from app.db.session import session_scope
from app.riot import get_api
from app.services.poller import participant_details
from app.services.registration import register_player

log = logging.getLogger("pekin.bootstrap")


def _find_participant(row: MatchParticipant, parts: list[dict], puuid: str | None) -> dict | None:
    """Participant JSON correspondant à une ligne : par puuid, sinon par champion (unique dans une partie)."""
    if puuid:
        for part in parts:
            if part.get("puuid") == puuid:
                return part
    # Compte re-lié depuis (autre puuid) : le champion identifie encore le joueur dans la partie
    for part in parts:
        if part.get("championName") == row.champion_name or (
            row.champion_id is not None and part.get("championId") == row.champion_id
        ):
            if row.team_side is None or part.get("teamId") == row.team_side:
                return part
    return None


def backfill_match_details(session: Session) -> int:
    """Renseigne les colonnes de détail (`double_kills`, `wards_placed`, `damage_share`…) des
    participations enregistrées avant leur ajout, à partir du `Match.raw_json` conservé.

    Une ligne est à traiter si `largest_multi_kill` est NULL et que le JSON de la partie existe.
    Même extraction que le poller (`participant_details`). Renvoie le nombre de lignes mises à jour.
    """
    pending = session.exec(
        select(MatchParticipant)
        .where(col(MatchParticipant.largest_multi_kill).is_(None))
        .order_by(col(MatchParticipant.match_id), col(MatchParticipant.id))
    ).all()
    if not pending:
        return 0
    player_ids = {row.player_id for row in pending}
    puuid_by_player = {
        player.id: player.puuid
        for player in session.exec(select(Player).where(col(Player.id).in_(list(player_ids)))).all()
    }
    updated = 0
    parts_cache: dict[str, list[dict] | None] = {}
    for row in pending:
        if row.match_id not in parts_cache:
            parts_cache[row.match_id] = _match_parts(session, row.match_id)
        parts = parts_cache[row.match_id]
        if not parts:
            continue
        part = _find_participant(row, parts, puuid_by_player.get(row.player_id))
        if part is None:
            continue
        for column, value in participant_details(part, parts).items():
            setattr(row, column, value)
        session.add(row)
        updated += 1
    if updated:
        session.commit()
    return updated


def _match_parts(session: Session, match_id: str) -> list[dict] | None:
    """Participants du JSON Match-V5 stocké ; None si absent ou illisible."""
    match = session.get(Match, match_id)
    if match is None or not match.raw_json:
        return None
    try:
        raw = json.loads(match.raw_json)
    except ValueError:
        return None
    info = raw.get("info") if isinstance(raw, dict) else None
    if not isinstance(info, dict):
        return None
    parts = [part for part in info.get("participants") or [] if isinstance(part, dict)]
    return parts or None


def ensure_challenge(session: Session | None = None) -> Challenge:
    """Renvoie la ligne `Challenge` unique, créée (depuis les settings) si absente."""
    if session is None:
        with session_scope() as own_session:
            challenge = ensure_challenge(own_session)
            own_session.refresh(challenge)  # attributs chargés avant de détacher l'objet
            return challenge
    challenge = session.exec(select(Challenge).order_by(Challenge.id)).first()
    if challenge is None:
        settings = get_settings()
        challenge = Challenge(games_per_day=settings.games_per_day, track_flex=settings.track_flex)
        session.add(challenge)
        session.commit()
        session.refresh(challenge)
    return challenge


async def load_players_yaml(path: Path) -> None:
    """Si la table `Player` est vide : applique `challenge:` et inscrit chaque entrée `players:`.

    Fichier absent, vide ou invalide → rien à faire (loggé). Tolère `players: []`.
    """
    if not path.exists():
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        log.warning("players.yaml illisible (%s) : ignoré", exc)
        return
    if not isinstance(data, dict):
        log.warning("players.yaml : structure inattendue, ignoré")
        return

    with session_scope() as session:
        if session.exec(select(Player)).first() is not None:
            return  # des joueurs existent déjà : le fichier ne sert qu'au premier démarrage

        challenge = ensure_challenge(session)
        config = data.get("challenge") or {}
        if isinstance(config, dict):
            name = str(config.get("name") or "").strip()
            games_per_day = config.get("games_per_day")
            if name:
                challenge.name = name
            if isinstance(games_per_day, int) and games_per_day > 0:
                challenge.games_per_day = games_per_day
            session.add(challenge)
            session.commit()

        entries = data.get("players") or []
        if not isinstance(entries, list):
            log.warning("players.yaml : `players` doit être une liste, ignoré")
            return
        api = get_api()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            display_name = str(entry.get("display_name") or "").strip()
            riot_id = entry.get("riot_id")
            try:
                player = await register_player(
                    session, api, display_name=display_name, riot_id=str(riot_id) if riot_id else None
                )
            except ValueError as exc:
                log.warning("players.yaml : « %s » ignoré (%s)", display_name or riot_id, exc)
                continue
            if player.link_error:
                log.warning("players.yaml : %s inscrit mais non lié (%s)", player.display_name, player.link_error)
            else:
                log.info("players.yaml : %s inscrit (%s)", player.display_name, player.riot_id or "sans compte")
