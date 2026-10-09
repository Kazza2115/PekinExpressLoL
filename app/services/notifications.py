"""Notifications Discord (webhook optionnel) et mise en forme des messages.

`send_discord` ne lève jamais : si l'URL du webhook est vide, ou si l'envoi
échoue (réseau, HTTP ≥ 400, timeout), elle renvoie `False` et logge. Les
formateurs renvoient du texte Discord (markdown) en français.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import httpx

from app.config import Settings, get_settings
from app.services.portal import share_url
from app.db.session import as_utc

if TYPE_CHECKING:
    from app.db.models import Challenge, MatchParticipant, Player, Team

log = logging.getLogger(__name__)

# Discord refuse les messages de plus de 2000 caractères : on garde une marge.
DISCORD_MAX_LENGTH = 1900
# Délai maximal d'un appel au webhook (secondes)
DISCORD_TIMEOUT_S = 5.0


def truncate(content: str, limit: int = DISCORD_MAX_LENGTH) -> str:
    """Coupe `content` à `limit` caractères (avec une ellipse) si nécessaire."""
    if len(content) <= limit:
        return content
    if limit <= 1:
        return content[:limit]
    return content[: limit - 1] + "…"


async def send_discord(
    content: str,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Poste `content` sur le webhook Discord configuré.

    Renvoie `True` si Discord a accepté le message, `False` sinon (webhook non
    configuré, erreur réseau, réponse HTTP ≥ 400). Jamais d'exception.
    `client` permet d'injecter un `httpx.AsyncClient` (tests : `MockTransport`).
    """
    settings = settings if settings is not None else get_settings()
    url = (settings.discord_webhook_url or "").strip()
    if not url:
        return False
    payload = {"content": truncate(content), "allowed_mentions": {"parse": []}}
    try:
        if client is not None:
            response = await client.post(url, json=payload, timeout=DISCORD_TIMEOUT_S)
        else:
            async with httpx.AsyncClient(timeout=DISCORD_TIMEOUT_S) as own_client:
                response = await own_client.post(url, json=payload)
    except Exception as exc:  # noqa: BLE001 — réseau, timeout, URL invalide…
        log.warning("Webhook Discord injoignable : %s", exc)
        return False
    if response.status_code >= 400:
        log.warning(
            "Webhook Discord : HTTP %s — %s", response.status_code, response.text[:200]
        )
        return False
    return True


# --------------------------------------------------------------------------- #
# Formateurs
# --------------------------------------------------------------------------- #


def _team_suffix(team: Team | None) -> str:
    """« (Duo Rouge) » ou chaîne vide si le joueur n'a pas encore de duo."""
    name = getattr(team, "name", None) if team is not None else None
    return f" ({name})" if name else ""


def format_lp_delta(lp_change: int) -> str:
    """« +21 LP », « −17 LP » (signe moins typographique), « ±0 LP »."""
    if lp_change > 0:
        return f"+{lp_change} LP"
    if lp_change < 0:
        return f"−{abs(lp_change)} LP"
    return "±0 LP"


def format_live_start(player: Player, team: Team | None, champion: str) -> str:
    """« 🔴 **Mike** (Duo Rouge) vient de lancer une partie — **Ahri** »."""
    return (
        f"🔴 **{player.display_name}**{_team_suffix(team)} vient de lancer une partie"
        f" — **{champion}**"
    )


def format_match_recorded(
    player: Player,
    team: Team | None,
    participant: MatchParticipant,
    lp_change: int | None,
    *,
    over_quota: bool = False,
    day_number: int | None = None,
) -> str:
    """« ✅ **Mike** (Duo Rouge) gagne avec **Ahri** · 7/2/9 · +21 LP ».

    Partie au-delà du quota quotidien : « · ⛔ hors quota (11e partie du jour, ne compte pas) ».
    """
    icon, verb = ("✅", "gagne") if participant.win else ("❌", "perd")
    parts = [
        f"{icon} **{player.display_name}**{_team_suffix(team)} {verb} avec"
        f" **{participant.champion_name}**",
        f"{participant.kills}/{participant.deaths}/{participant.assists}",
    ]
    if lp_change is not None:
        parts.append(format_lp_delta(lp_change))
    queue = getattr(participant.queue, "value", participant.queue)
    if queue == "FLEX":
        parts.append("Flex")
    if over_quota:
        rank = f"{day_number}e partie du jour, " if day_number else ""
        parts.append(f"⛔ hors quota ({rank}ne compte pas)")
    return " · ".join(parts)


def format_challenge_started(challenge: Challenge, *, settings: Settings | None = None) -> str:
    """Annonce du lancement du challenge (objectif quotidien + lien vers le classement)."""
    settings = settings if settings is not None else get_settings()
    from datetime import datetime, timezone  # import local : seul usage du module

    start_at = as_utc(getattr(challenge, "start_at", None))
    end_at = as_utc(getattr(challenge, "end_at", None))
    upcoming = start_at is not None and start_at > datetime.now(timezone.utc)
    lines = [
        f"🚀 **{challenge.name}** : " + ("tout est prêt !" if upcoming else "le challenge commence !"),
        f"Objectif : **{challenge.games_per_day} parties par jour** et par joueur (au-delà, elles ne"
        " comptent pas) — le duo qui gagne le plus de LP l'emporte.",
    ]
    if start_at is not None:
        lines.append(f"Début : {start_at.astimezone(settings.tz).strftime('%d/%m/%Y %H:%M')}")
    if end_at is not None:
        lines.append(f"Fin : {end_at.astimezone(settings.tz).strftime('%d/%m/%Y %H:%M')}")
    lines.append(f"Classement : {share_url(settings)}/dashboard")
    return "\n".join(lines)


def _player_label(player: Any, player_id: int) -> str:
    """Nom affichable d'un joueur (objet `Player`, chaîne ou inconnu)."""
    if player is None:
        return f"Joueur {player_id}"
    name = getattr(player, "display_name", None)
    return str(name) if name else str(player)


def format_draw_done(teams: list[dict], players_by_id: Mapping[int, Any]) -> str:
    """Annonce des duos tirés : une ligne par duo, dans l'ordre de tirage.

    `teams` au format `DrawResult.teams` (`{id, name, color, slot, player_ids}`),
    `players_by_id` : id → `Player` (ou simple pseudo).
    """
    lines = ["🎡 **Les duos sont tirés !**"]
    for team in sorted(teams, key=lambda t: t.get("slot", 0)):
        names = [
            _player_label(players_by_id.get(pid), pid) for pid in team.get("player_ids", [])
        ]
        lines.append(f"• **{team.get('name', 'Duo')}** : {' & '.join(names)}")
    return "\n".join(lines)
