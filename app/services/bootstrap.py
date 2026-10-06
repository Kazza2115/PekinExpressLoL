"""Amorçage au démarrage : ligne `Challenge` unique et joueurs de `players.yaml`."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import Challenge, Player
from app.db.session import session_scope
from app.riot import get_api
from app.services.registration import register_player

log = logging.getLogger("pekin.bootstrap")


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
