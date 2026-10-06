"""Inscription des joueurs et liaison de leur compte Riot.

- `parse_riot_id("Nom#TAG")` → ("Nom", "TAG"), `ValueError` (message FR) sinon ;
- `register_player` crée le joueur (pseudo unique, insensible à la casse) puis le lie
  si un Riot ID est fourni ;
- `link_player` : Account-V1 → puuid, Summoner-V4 → icône / niveau, League-V4 →
  premier(s) `RankSnapshot`. Une erreur Riot ne lève pas : elle est traduite dans
  `player.link_error` (le joueur reste « à lier »). Un puuid déjà utilisé par un
  autre joueur lève `ValueError`.
"""

from __future__ import annotations

import logging
import re

import httpx
from sqlalchemy import func
from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import QUEUE_TYPES, Player, Queue, RankSnapshot, utcnow
from app.events import bus
from app.riot.base import (
    LeagueEntryDTO,
    RiotAPI,
    RiotError,
    RiotNotFound,
    RiotRateLimited,
    RiotUnauthorized,
)
from app.services.stats import APEX_TIERS, absolute_lp

log = logging.getLogger("pekin.registration")

DISPLAY_NAME_MIN, DISPLAY_NAME_MAX = 2, 20
GAME_NAME_MIN, GAME_NAME_MAX = 3, 16
TAG_LINE_RE = re.compile(r"^[^\W_]{3,5}$")  # lettres/chiffres Unicode, sans espace ni #

ERR_NOT_FOUND = "Riot ID introuvable : vérifie le pseudo et le tag."
ERR_UNAUTHORIZED = "Clé Riot invalide ou expirée (voir .env)."
ERR_RATE_LIMITED = "API Riot saturée, réessaie dans une minute."


def parse_riot_id(raw: str | None) -> tuple[str, str]:
    """"Nom#TAG" (espaces autour tolérés) → ("Nom", "TAG") ; `ValueError` si invalide."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("Indique ton Riot ID au format Pseudo#TAG (ex. La Peace#CHILL).")
    if "#" not in text:
        raise ValueError("Format attendu : Pseudo#TAG (ex. La Peace#CHILL).")
    game_name, _, tag_line = text.partition("#")
    game_name, tag_line = game_name.strip(), tag_line.strip()
    if not GAME_NAME_MIN <= len(game_name) <= GAME_NAME_MAX:
        raise ValueError(f"Le pseudo Riot doit faire entre {GAME_NAME_MIN} et {GAME_NAME_MAX} caractères.")
    if not TAG_LINE_RE.match(tag_line):
        raise ValueError("Le tag doit faire 3 à 5 lettres ou chiffres (ex. EUW, CHILL).")
    return game_name, tag_line


def _link_error_message(exc: Exception) -> str:
    if isinstance(exc, RiotNotFound):
        return ERR_NOT_FOUND
    if isinstance(exc, RiotUnauthorized):
        return ERR_UNAUTHORIZED
    if isinstance(exc, RiotRateLimited):
        return ERR_RATE_LIMITED
    return f"Erreur API Riot : {exc}"


def _snapshot_from_entry(player_id: int, queue: Queue, entry: LeagueEntryDTO | None) -> RankSnapshot:
    """Snapshot de référence : unranked (tier None) si aucune entrée pour la file."""
    if entry is None:
        return RankSnapshot(player_id=player_id, queue=queue, tier=None, rank=None, lp=0, absolute_lp=None)
    tier = entry.tier.upper()
    # Master+ : Riot renvoie rank "I", sans signification → None (même normalisation que le poller)
    rank = entry.rank.upper() if entry.rank and tier not in APEX_TIERS else None
    return RankSnapshot(
        player_id=player_id,
        queue=queue,
        tier=tier,
        rank=rank,
        lp=entry.league_points,
        wins=entry.wins,
        losses=entry.losses,
        hot_streak=entry.hot_streak,
        absolute_lp=absolute_lp(tier, rank, entry.league_points),
    )


def _player_event(player: Player) -> dict:
    return {
        "player_id": player.id,
        "display_name": player.display_name,
        "riot_id": player.riot_id,
        "is_linked": player.is_linked,
        "link_error": player.link_error,
    }


async def register_player(
    session: Session,
    api: RiotAPI,
    *,
    display_name: str,
    riot_id: str | None,
) -> Player:
    """Crée un joueur (pseudo unique) et lie son compte Riot si `riot_id` est fourni.

    Un Riot ID mal formé ou déjà lié à un autre joueur lève `ValueError` sans créer
    le joueur ; une erreur de l'API Riot crée le joueur avec `link_error` renseigné.
    """
    name = (display_name or "").strip()
    if not DISPLAY_NAME_MIN <= len(name) <= DISPLAY_NAME_MAX:
        raise ValueError(f"Le pseudo doit faire entre {DISPLAY_NAME_MIN} et {DISPLAY_NAME_MAX} caractères.")
    existing = session.exec(select(Player).where(func.lower(Player.display_name) == name.lower())).first()
    if existing is not None:
        raise ValueError("Ce pseudo est déjà pris.")

    riot_id = (riot_id or "").strip() or None
    if riot_id is not None:
        parse_riot_id(riot_id)  # valide le format avant toute écriture

    player = Player(display_name=name)
    session.add(player)
    session.flush()  # id attribué ; la ligne n'est validée qu'au commit
    try:
        if riot_id is not None:
            await link_player(session, api, player, riot_id)
        else:
            session.commit()
    except ValueError:
        session.rollback()  # compte Riot déjà lié à un autre joueur : on ne garde pas le joueur
        raise
    session.refresh(player)
    bus.publish("player_registered", _player_event(player))
    return player


async def link_player(session: Session, api: RiotAPI, player: Player, riot_id: str) -> Player:
    """Lie (ou corrige) le compte Riot d'un joueur ; voir docstring du module."""
    game_name, tag_line = parse_riot_id(riot_id)
    settings = get_settings()

    try:
        account = await api.get_account_by_riot_id(game_name, tag_line)
        other = session.exec(select(Player).where(Player.puuid == account.puuid, Player.id != player.id)).first()
        if other is not None:
            raise ValueError(f"Ce compte Riot est déjà lié à {other.display_name}.")
        summoner = await api.get_summoner_by_puuid(account.puuid)
        entries = await api.get_league_entries_by_puuid(account.puuid)
    except (RiotError, httpx.HTTPError) as exc:
        player.link_error = _link_error_message(exc)
        if not player.is_linked:
            # Pas encore lié : on garde le Riot ID saisi pour pré-remplir le formulaire
            player.game_name, player.tag_line = game_name, tag_line
        log.warning("Liaison de %s (%s#%s) impossible : %s", player.display_name, game_name, tag_line, exc)
        session.add(player)
        session.commit()
        session.refresh(player)
        return player

    if player.puuid and player.puuid != account.puuid:
        # Changement de compte : l'historique de rang appartenait à l'ancien compte
        for snapshot in session.exec(select(RankSnapshot).where(RankSnapshot.player_id == player.id)).all():
            session.delete(snapshot)

    player.puuid = account.puuid
    player.game_name = account.game_name or game_name
    player.tag_line = account.tag_line or tag_line
    player.summoner_id = summoner.summoner_id
    player.profile_icon_id = summoner.profile_icon_id
    player.summoner_level = summoner.summoner_level
    player.link_error = None
    player.linked_at = utcnow()
    session.add(player)

    queues = [Queue.SOLO] + ([Queue.FLEX] if settings.track_flex else [])
    by_type = {entry.queue_type: entry for entry in entries}
    assert player.id is not None
    for queue in queues:
        session.add(_snapshot_from_entry(player.id, queue, by_type.get(QUEUE_TYPES[queue])))
    session.commit()
    session.refresh(player)

    solo = by_type.get(QUEUE_TYPES[Queue.SOLO])
    bus.publish(
        "player_linked",
        {
            **_player_event(player),
            "tier": solo.tier if solo else None,
            "rank": solo.rank if solo else None,
            "lp": solo.league_points if solo else 0,
        },
    )
    return player
