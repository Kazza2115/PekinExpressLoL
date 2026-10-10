"""Notifications Discord (webhook optionnel) et mise en forme des messages.

`send_discord` ne lève jamais : si l'URL du webhook est vide, ou si l'envoi
échoue (réseau, HTTP ≥ 400, timeout), elle renvoie `False` et logge. Les
formateurs `format_*` renvoient la ligne de texte du message (markdown, en
français) ; les `*_embed` / `build_*_message` les cartes Discord (embeds) qui
l'accompagnent : statistiques de la partie, rang, GIF selon le résultat. Le rôle
`DISCORD_ROLE_ID` est mentionné en tête de chaque message.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import httpx

from app.config import Settings, get_settings
from app.db.session import as_utc
from app.riot import ddragon
from app.services.portal import share_link

if TYPE_CHECKING:
    from app.db.models import Challenge, MatchParticipant, Player, Team

log = logging.getLogger(__name__)

# Discord refuse les messages de plus de 2000 caractères : on garde une marge.
DISCORD_MAX_LENGTH = 1900
# Délai maximal d'un appel au webhook (secondes)
DISCORD_TIMEOUT_S = 5.0
# Limites Discord des cartes (embeds) : au plus 10 par message
MAX_EMBEDS = 10
EMBED_TEXT_LIMITS = {"title": 256, "description": 4096}
FIELD_NAME_LIMIT = 256
FIELD_VALUE_LIMIT = 1024
MAX_FIELDS = 25

# Couleurs du liseré des cartes
COLOR_WIN = 0x23A55A
COLOR_LOSS = 0xF23F43
COLOR_LIVE = 0xE67E22
COLOR_INFO = 0xF0B232
COLOR_JOKER = 0x9B59B6
COLOR_DRAW = 0x5865F2

QUEUE_NAMES = {"SOLO": "Solo/Duo", "FLEX": "Flex"}
QUEUE_NAMES_BY_ID = {420: "Solo/Duo", 440: "Flex"}
MULTI_KILL_LABELS = {3: "Triple kill", 4: "Quadra kill", 5: "PENTAKILL"}
FOOTER_TEXT = "Pékin Express LoL"


def truncate(content: str, limit: int = DISCORD_MAX_LENGTH) -> str:
    """Coupe `content` à `limit` caractères (avec une ellipse) si nécessaire."""
    if len(content) <= limit:
        return content
    if limit <= 1:
        return content[:limit]
    return content[: limit - 1] + "…"


def role_mention(settings: Settings | None = None) -> str:
    """« <@&ID> » du rôle à notifier (DISCORD_ROLE_ID), ou chaîne vide."""
    settings = settings if settings is not None else get_settings()
    role = (getattr(settings, "discord_role_id", "") or "").strip()
    return f"<@&{role}>" if role else ""


def clamp_embed(embed: Mapping[str, Any]) -> dict[str, Any]:
    """Copie d'une carte respectant les limites Discord (textes tronqués, 25 champs au plus)."""
    clean = dict(embed)
    for key, limit in EMBED_TEXT_LIMITS.items():
        if isinstance(clean.get(key), str):
            clean[key] = truncate(clean[key], limit)
    if isinstance(clean.get("author"), Mapping):
        clean["author"] = {**clean["author"], "name": truncate(str(clean["author"].get("name") or ""), 256)}
    if isinstance(clean.get("footer"), Mapping):
        clean["footer"] = {**clean["footer"], "text": truncate(str(clean["footer"].get("text") or ""), 2048)}
    fields = clean.get("fields")
    if isinstance(fields, list):
        clean["fields"] = [
            {
                **field,
                "name": truncate(str(field.get("name") or "\u200b"), FIELD_NAME_LIMIT),
                "value": truncate(str(field.get("value") or "\u200b"), FIELD_VALUE_LIMIT),
            }
            for field in fields[:MAX_FIELDS]
            if isinstance(field, Mapping)
        ]
    return clean


@dataclass
class DiscordSendResult:
    """Résultat détaillé d'un envoi (diagnostic du test de l'admin)."""

    sent: bool
    status: int | None = None  # code HTTP de Discord (None : pas d'envoi ou réseau KO)
    error: str | None = None
    mention_roles: list[str] | None = None  # rôles notifiés selon Discord (`wait=true` seulement)


async def post_discord(
    content: str = "",
    *,
    embeds: Sequence[Mapping[str, Any]] | None = None,
    mention: bool = True,
    wait: bool = False,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> DiscordSendResult:
    """Envoi sur le webhook, avec le détail de la réponse. Jamais d'exception.

    `wait=True` demande à Discord de renvoyer le message créé : `mention_roles` dit alors si la
    mention du rôle a vraiment été prise en compte (rôle existant et mentionnable).
    """
    settings = settings if settings is not None else get_settings()
    url = (settings.discord_webhook_url or "").strip()
    if not url:
        return DiscordSendResult(sent=False, error="DISCORD_WEBHOOK_URL est vide")
    mention_text = role_mention(settings) if mention else ""
    if content and mention_text:
        text = f"{mention_text} {truncate(content, DISCORD_MAX_LENGTH - len(mention_text) - 1)}"
    else:
        text = truncate(content) if content else mention_text
    cards = [clamp_embed(embed) for embed in (embeds or [])[:MAX_EMBEDS]]
    if not text and not cards:
        return DiscordSendResult(sent=False, error="message vide")
    allowed: dict[str, Any] = {"parse": []}
    if mention_text:
        allowed["roles"] = [settings.discord_role_id.strip()]
    payload: dict[str, Any] = {"content": text, "allowed_mentions": allowed}
    if cards:
        payload["embeds"] = cards
    try:
        # `wait` ajouté aux paramètres déjà présents (ex. `thread_id` d'un webhook de fil)
        target = httpx.URL(url).copy_merge_params({"wait": "true"}) if wait else url
        if client is not None:
            response = await client.post(target, json=payload, timeout=DISCORD_TIMEOUT_S)
        else:
            async with httpx.AsyncClient(timeout=DISCORD_TIMEOUT_S) as own_client:
                response = await own_client.post(target, json=payload)
    except Exception as exc:  # noqa: BLE001 — réseau, timeout, URL invalide…
        log.warning("Webhook Discord injoignable : %s", exc)
        return DiscordSendResult(sent=False, error=f"Discord injoignable ({type(exc).__name__})")
    if response.status_code >= 400:
        log.warning(
            "Webhook Discord : HTTP %s — %s", response.status_code, response.text[:200]
        )
        return DiscordSendResult(sent=False, status=response.status_code, error=f"Discord a refusé le message (HTTP {response.status_code})")
    mention_roles = None
    if wait:
        try:
            body = response.json()
            roles = body.get("mention_roles") if isinstance(body, dict) else None
            mention_roles = [str(role) for role in roles] if isinstance(roles, list) else None
        except ValueError:
            mention_roles = None
    return DiscordSendResult(sent=True, status=response.status_code, mention_roles=mention_roles)


async def send_discord(
    content: str = "",
    *,
    embeds: Sequence[Mapping[str, Any]] | None = None,
    mention: bool = True,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Poste `content` (et les cartes `embeds`) sur le webhook Discord configuré.

    Le rôle DISCORD_ROLE_ID est mentionné en tête du message (sauf `mention=False`) ; aucune
    autre mention n'est autorisée (@everyone, pseudos). Renvoie `True` si Discord a accepté le
    message, `False` sinon (webhook non configuré, erreur réseau, réponse HTTP ≥ 400). Jamais
    d'exception. `client` permet d'injecter un `httpx.AsyncClient` (tests : `MockTransport`).
    """
    result = await post_discord(content, embeds=embeds, mention=mention, settings=settings, client=client)
    return result.sent


# --------------------------------------------------------------------------- #
# Formateurs
# --------------------------------------------------------------------------- #


def headline(text: str) -> str:
    """Première ligne d'un message (le détail est alors dans la carte qui l'accompagne)."""
    return text.strip().splitlines()[0] if text.strip() else ""


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
    outside_window: bool = False,
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
    if outside_window:
        parts.append("⏱ hors des heures du challenge (ne compte pas)")
    elif over_quota:
        rank = f"{day_number}e partie du jour, " if day_number else ""
        parts.append(f"⛔ hors quota ({rank}ne compte pas)")
    return " · ".join(parts)


def format_challenge_started(challenge: Challenge, *, settings: Settings | None = None) -> str:
    """Annonce du lancement du challenge (objectif quotidien + lien vers le classement)."""
    settings = settings if settings is not None else get_settings()
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
    lines.append(f"Classement : {share_link('/dashboard', settings)}")
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


def format_joker_used(team_name: str, player_name: str | None, extra_games: int, limit: int) -> str:
    """« 🃏 **Duo Rouge** active son joker (par Mike) : 13 parties comptées aujourd'hui au lieu de 10 »."""
    by = f" (par {player_name})" if player_name else ""
    return (
        f"🃏 **{team_name}** active son joker{by} : **{limit + extra_games} parties** comptées aujourd'hui"
        f" au lieu de {limit}, pour les parties terminées à partir de maintenant."
    )


# --------------------------------------------------------------------------- #
# Cartes Discord (embeds)
# --------------------------------------------------------------------------- #


def _fr_decimal(value: float, digits: int = 1) -> str:
    """7.25 → « 7,3 » (virgule décimale)."""
    return f"{value:.{digits}f}".replace(".", ",")


def _fr_int(value: float) -> str:
    """32456 → « 32 456 »."""
    return f"{round(value):,}".replace(",", " ")


def _fr_thousands(value: float) -> str:
    """35512 → « 35,5k » ; < 1000 → entier."""
    return f"{_fr_decimal(value / 1000)}k" if abs(value) >= 1000 else str(round(value))


def _mmss(seconds: int | None) -> str:
    """1947 → « 32:27 »."""
    minutes, secs = divmod(max(0, int(seconds or 0)), 60)
    return f"{minutes}:{secs:02d}"


def _ordinal(number: int) -> str:
    """1 → « 1er », 4 → « 4e »."""
    return "1er" if number == 1 else f"{number}e"


def _signed(value: int) -> str:
    """+1 250 / −830 / 0 (signe moins typographique)."""
    if value > 0:
        return f"+{_fr_int(value)}"
    if value < 0:
        return f"−{_fr_int(abs(value))}"
    return "0"


def queue_name(queue: Any) -> str:
    """File de la partie : « Solo/Duo » ou « Flex »."""
    return QUEUE_NAMES.get(str(getattr(queue, "value", queue) or "SOLO"), "Solo/Duo")


def champion_label(champion_name: str | None) -> str:
    """Nom affiché du champion (« MonkeyKing » → « Wukong »)."""
    return ddragon.champion_display_name(champion_name) or "Champion"


def _champion_thumbnail(champion_name: str | None) -> dict[str, str] | None:
    url = ddragon.champion_icon_url(ddragon.CURRENT_VERSION, champion_name)
    return {"url": url} if url else None


def _author(player: Player, settings: Settings) -> dict[str, str]:
    """En-tête de carte : Riot ID du joueur, son icône d'invocateur, lien vers sa fiche."""
    author = {
        "name": player.riot_id or player.display_name,
        "url": share_link(f"/player/{player.id}", settings) if player.id is not None else share_link("/", settings),
    }
    icon = ddragon.profile_icon_url(ddragon.CURRENT_VERSION, player.profile_icon_id)
    if icon:
        author["icon_url"] = icon
    return author


def _footer(team: Team | None) -> dict[str, str]:
    name = getattr(team, "name", None) if team is not None else None
    return {"text": f"{FOOTER_TEXT} · {name}" if name else FOOTER_TEXT}


def _iso(value: datetime | None) -> str | None:
    value = as_utc(value)
    return value.isoformat() if value is not None else None


def _platform_slug(settings: Settings) -> str:
    """« euw1 » → « euw » (liens op.gg)."""
    platform = (getattr(settings, "riot_platform", "") or "euw1").lower()
    return platform.rstrip("0123456789") or "euw"


def _external_links(player: Player, match_id: str | None, settings: Settings) -> list[str]:
    """Liens dpm.lol (partie) et op.gg (profil) quand le Riot ID est connu."""
    if not (player.game_name and player.tag_line):
        return []
    slug = f"{quote(player.game_name)}-{quote(player.tag_line)}"
    links = []
    if match_id:
        links.append(f"[dpm.lol](https://dpm.lol/{slug}?match={quote(match_id.split('_', 1)[-1])})")
    links.append(f"[op.gg](https://op.gg/lol/summoners/{_platform_slug(settings)}/{slug})")
    return links


@dataclass
class MatchNotice:
    """Une partie terminée d'un joueur du challenge, prête à être annoncée."""

    player: Player
    team: Team | None
    participant: MatchParticipant
    lp_change: int | None = None
    rank_label: str | None = None  # rang après la partie (« Diamond III · 38 LP »)
    highlights: dict[str, Any] | None = None  # `scoreboard.player_highlights`
    day_number: int | None = None  # n-ième partie du jour
    day_limit: int | None = None  # parties comptées ce jour-là (joker compris)
    over_quota: bool = False
    outside_window: bool = False


def match_title(notice: MatchNotice) -> str:
    """« Mike a gagné 19 LP (Solo/Duo) », « Mike a perdu 17 LP (Solo/Duo) »."""
    name = notice.player.display_name
    queue = queue_name(notice.participant.queue)
    lp = notice.lp_change
    if lp is None:
        return f"{name} a {'gagné' if notice.participant.win else 'perdu'} sa partie ({queue})"
    if lp > 0:
        return f"{name} a gagné {lp} LP ({queue})"
    if lp < 0:
        return f"{name} a perdu {abs(lp)} LP ({queue})"
    return f"{name} : ±0 LP ({queue})"


def match_embed(notice: MatchNotice, *, settings: Settings | None = None) -> dict[str, Any]:
    """Carte de résultat façon tracker : rang, KDA, durée, score, CS/min, dégâts, vision…"""
    settings = settings if settings is not None else get_settings()
    part = notice.participant
    h = notice.highlights or {}
    duration = int(h.get("duration_s") or part.game_duration or 0)
    minutes = duration / 60 if duration > 0 else 0
    win = bool(part.win)

    lines = []
    if notice.rank_label:
        lines.append(f"**{notice.rank_label}**")
    # Abandon marqué pour les deux équipes par Riot : seule l'équipe perdante a abandonné
    result = ("✅ Victoire" if win else "❌ Défaite") + (
        (" par abandon adverse 🏳️" if win else " par abandon 🏳️") if part.surrendered and not part.is_remake else ""
    )
    lines.append(f"{result} avec **{champion_label(part.champion_name)}**")
    multi = int(h.get("largest_multi_kill") or 0)
    if multi >= 3:
        lines.append(f"🔥 **{MULTI_KILL_LABELS[min(multi, 5)]} !**")
    if notice.outside_window:
        lines.append("⏱ **Hors des heures du challenge** : cette partie ne compte pas.")
    elif notice.over_quota:
        rank = f"{_ordinal(notice.day_number)} partie du jour" if notice.day_number else "Partie"
        limit = f" (limite : {notice.day_limit})" if notice.day_limit else ""
        lines.append(f"⛔ **Hors quota** : {rank}{limit}, elle ne compte pas pour le duo.")

    kills, deaths, assists = int(part.kills or 0), int(part.deaths or 0), int(part.assists or 0)
    ratio = "Parfait" if deaths == 0 else _fr_decimal((kills + assists) / deaths, 2)
    fields: list[dict[str, Any]] = [
        {"name": "KDA", "value": f"{kills}/{deaths}/{assists} ({ratio})", "inline": True},
        {"name": "Durée", "value": _mmss(duration), "inline": True},
    ]
    if h.get("score") is not None:
        rank_in_game = h.get("badge") or f"{_ordinal(int(h.get('place') or 0))}/{h.get('players_count') or 10}"
        fields.append({"name": "Score", "value": f"{_fr_decimal(float(h['score']), 2)} ({rank_in_game})", "inline": True})
    cs = int(h.get("cs") if h.get("cs") is not None else part.cs or 0)
    if minutes:
        fields.append({"name": "CS/min", "value": f"{_fr_decimal(cs / minutes)} ({cs})", "inline": True})
    if h.get("pings") is not None:
        fields.append({"name": "Pings", "value": str(h["pings"]), "inline": True})
    damage = int(h.get("damage") if h.get("damage") is not None else part.damage_to_champions or 0)
    per_min = f" ({_fr_int(damage / minutes)}/min)" if minutes else ""
    fields.append({"name": "Dégâts", "value": f"{_fr_thousands(damage)}{per_min}", "inline": True})
    vision = int(h.get("vision_score") if h.get("vision_score") is not None else part.vision_score or 0)
    if minutes:
        fields.append({"name": "Vision/min", "value": _fr_decimal(vision / minutes, 2), "inline": True})
    if h.get("team_luck"):
        fields.append({"name": "Chance d'équipe", "value": str(h["team_luck"]), "inline": True})
    kp = h.get("kill_participation", part.kill_participation)
    if kp is not None:
        fields.append({"name": "Participation", "value": f"{round(float(kp))} %", "inline": True})
    if h.get("gold_diff_lane") is not None:
        fields.append({"name": "Écart d'or (voie)", "value": _signed(int(h["gold_diff_lane"])), "inline": True})
    if notice.day_number:
        limit = f" / {notice.day_limit}" if notice.day_limit else ""
        fields.append({"name": "Partie du jour", "value": f"{_ordinal(notice.day_number)}{limit}", "inline": True})

    scoreboard_link = share_link(f"/player/{notice.player.id}#match-{part.match_id}", settings)
    links = [f"[📊 Tableau des scores]({scoreboard_link})", *_external_links(notice.player, part.match_id, settings)]
    fields.append({"name": "Liens", "value": " · ".join(links), "inline": False})

    embed: dict[str, Any] = {
        "author": _author(notice.player, settings),
        "title": match_title(notice),
        "url": scoreboard_link,
        "description": "\n".join(lines),
        "color": COLOR_WIN if win else COLOR_LOSS,
        "fields": fields,
        "footer": _footer(notice.team),
    }
    thumbnail = _champion_thumbnail(part.champion_name)
    if thumbnail:
        embed["thumbnail"] = thumbnail
    timestamp = _iso(part.game_end) or _iso(part.game_start)
    if timestamp:
        embed["timestamp"] = timestamp
    return embed


def group_match_notices(notices: Sequence[MatchNotice]) -> list[list[MatchNotice]]:
    """Regroupe les deux joueurs d'un même duo dans la même partie et le même camp (un seul message)."""
    groups: dict[tuple[Any, ...], list[MatchNotice]] = {}
    for notice in notices:
        team_id = notice.player.team_id
        owner = ("duo", team_id) if team_id is not None else ("joueur", notice.player.id)
        key = (notice.participant.match_id, notice.participant.team_side, *owner)
        groups.setdefault(key, []).append(notice)
    return list(groups.values())


def is_duo_group(notices: Sequence[MatchNotice]) -> bool:
    """Les deux joueurs d'un même duo dans la même partie (sinon : un joueur sans son coéquipier)."""
    return len(notices) >= 2 and notices[0].team is not None


def build_match_message(
    notices: Sequence[MatchNotice], *, gif: str | None = None, settings: Settings | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """(texte, cartes) d'un résultat : un joueur seul, ou les deux joueurs d'un duo dans la même partie.

    `gif` (victoire ou défaite, voir `gifs.find_gif`) : sous la carte du joueur, ou dans une carte
    « Victoire / Défaite en duo » quand le duo a joué ensemble.
    """
    settings = settings if settings is not None else get_settings()
    first = notices[0]
    win = bool(first.participant.win)
    team = first.team
    duo = is_duo_group(notices)
    embeds = [match_embed(notice, settings=settings) for notice in notices]
    if duo:
        names = " & ".join(n.player.display_name for n in notices)
        lps = [n.lp_change for n in notices]
        total = f" · {format_lp_delta(sum(lps))}" if all(lp is not None for lp in lps) else ""  # type: ignore[misc]
        verb = "gagne" if win else "perd"
        content = f"{'🎉' if win else '💀'} **{team.name}** ({names}) {verb} en duo{total}"
        if gif:
            embeds.append(
                {
                    "title": f"{'🎉 Victoire' if win else '💀 Défaite'} en duo pour {team.name}{total}",
                    "color": COLOR_WIN if win else COLOR_LOSS,
                    "image": {"url": gif},
                }
            )
    else:
        content = format_match_recorded(
            first.player,
            team,
            first.participant,
            first.lp_change,
            over_quota=first.over_quota,
            day_number=first.day_number,
            outside_window=first.outside_window,
        )
        content = content.replace(f"**{first.participant.champion_name}**", f"**{champion_label(first.participant.champion_name)}**")
        if gif:
            embeds[-1]["image"] = {"url": gif}
    return content, embeds


@dataclass
class LiveNotice:
    """Un joueur du challenge qui vient de lancer une partie classée."""

    player: Player
    team: Team | None
    champion_name: str
    queue_id: int | None = None
    rank_label: str | None = None


def build_live_message(
    entries: Sequence[LiveNotice], *, settings: Settings | None = None, now: datetime | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """(texte, carte) d'un début de partie : un joueur seul, ou un duo lancé ensemble."""
    settings = settings if settings is not None else get_settings()
    first = entries[0]
    queue = QUEUE_NAMES_BY_ID.get(int(first.queue_id or 0), "Classée")
    live_link = share_link("/dashboard", settings)
    duo = len(entries) >= 2 and first.team is not None
    if duo:
        names = " & ".join(e.player.display_name for e in entries)
        champions = " & ".join(f"**{champion_label(e.champion_name)}**" for e in entries)
        content = f"🔴 **{first.team.name}** ({names}) lance une partie en duo — {champions}"  # type: ignore[union-attr]
        title = f"🔴 {first.team.name} est en partie ({queue})"  # type: ignore[union-attr]
    else:
        content = format_live_start(first.player, first.team, champion_label(first.champion_name))
        title = f"🔴 {first.player.display_name} vient de lancer une partie ({queue})"
    lines = [
        f"**{e.player.display_name}** · {champion_label(e.champion_name)} · {e.rank_label or 'Unranked'}" for e in entries
    ]
    lines.append(f"[📺 Suivre en direct]({live_link})")
    embed: dict[str, Any] = {
        "title": title,
        "url": live_link,
        "description": "\n".join(lines),
        "color": COLOR_LIVE,
        "footer": _footer(first.team),
        "timestamp": (now or datetime.now(timezone.utc)).isoformat(),
    }
    if not duo:
        embed["author"] = _author(first.player, settings)
    thumbnail = _champion_thumbnail(first.champion_name)
    if thumbnail:
        embed["thumbnail"] = thumbnail
    return content, [embed]


def challenge_started_embed(challenge: Challenge, *, settings: Settings | None = None) -> dict[str, Any]:
    """Carte du lancement : objectif, dates, lien vers le classement."""
    settings = settings if settings is not None else get_settings()
    start_at = as_utc(getattr(challenge, "start_at", None))
    end_at = as_utc(getattr(challenge, "end_at", None))
    upcoming = start_at is not None and start_at > datetime.now(timezone.utc)
    fields = []
    if start_at is not None:
        fields.append({"name": "Début", "value": start_at.astimezone(settings.tz).strftime("%d/%m/%Y %H:%M"), "inline": True})
    if end_at is not None:
        fields.append({"name": "Fin", "value": end_at.astimezone(settings.tz).strftime("%d/%m/%Y %H:%M"), "inline": True})
    link = share_link("/dashboard", settings)
    fields.append({"name": "Classement", "value": f"[🏆 Voir le classement]({link})", "inline": False})
    return {
        "title": f"🚀 {challenge.name} : " + ("tout est prêt !" if upcoming else "le challenge commence !"),
        "url": link,
        "description": (
            f"**{challenge.games_per_day} parties par jour** et par joueur (au-delà, elles ne comptent pas)."
            "\nLe duo qui gagne le plus de LP l'emporte."
        ),
        "color": COLOR_INFO,
        "fields": fields,
        "footer": {"text": FOOTER_TEXT},
    }


def draw_embed(teams: list[dict], players_by_id: Mapping[int, Any]) -> dict[str, Any]:
    """Carte du tirage : un champ par duo."""
    return {
        "title": "🎡 Les duos sont tirés !",
        "color": COLOR_DRAW,
        "fields": [
            {
                "name": str(team.get("name", "Duo")),
                "value": " & ".join(_player_label(players_by_id.get(pid), pid) for pid in team.get("player_ids", []))
                or "—",
                "inline": True,
            }
            for team in sorted(teams, key=lambda t: t.get("slot", 0))
        ],
        "footer": {"text": FOOTER_TEXT},
    }


def joker_embed(team_name: str, player_name: str | None, extra_games: int, limit: int) -> dict[str, Any]:
    """Carte du joker : parties comptées aujourd'hui au lieu de la limite habituelle."""
    by = f" (par {player_name})" if player_name else ""
    return {
        "title": f"🃏 {team_name} active son joker",
        "description": (
            f"**{limit + extra_games} parties** comptées aujourd'hui au lieu de {limit}{by},"
            " pour les parties terminées à partir de maintenant."
        ),
        "color": COLOR_JOKER,
        "footer": {"text": FOOTER_TEXT},
    }


def build_start_announcement(
    name: str,
    games_per_day: int,
    end_at: datetime | None,
    teams: Sequence[tuple[str, Sequence[str]]],
    *,
    gif: str | None = None,
    gif_page: str | None = None,
    settings: Settings | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Annonce « c'est parti » à l'heure du début : objectif, duos, fin, lien du classement, GIF.

    `gif_page` : page du GIF à mettre en lien quand son image directe est introuvable (Discord
    l'affiche alors lui-même sous le message).
    """
    settings = settings if settings is not None else get_settings()
    link = share_link("/dashboard", settings)
    content = f"🚀 **{name}** : c'est parti, le challenge commence maintenant ! Bonne chance à tous les duos 🍀"
    if gif_page and not gif:
        content += f"\n{gif_page}"
    lines = [
        f"**{games_per_day} parties par jour** et par joueur (au-delà, elles ne comptent pas).",
        "Le duo qui gagne le plus de LP l'emporte.",
    ]
    end = as_utc(end_at)
    if end is not None:
        lines.append(f"Fin : **{end.astimezone(settings.tz).strftime('%d/%m/%Y à %H:%M')}**")
    fields = [
        {"name": team_name, "value": " & ".join(players) or "—", "inline": True}
        for team_name, players in teams
    ]
    fields.append({"name": "Classement", "value": f"[🏆 Suivre le challenge en direct]({link})", "inline": False})
    embed: dict[str, Any] = {
        "title": f"🚀 C'est parti pour le {name} !",
        "url": link,
        "description": "\n".join(lines),
        "color": COLOR_INFO,
        "fields": fields,
        "footer": {"text": FOOTER_TEXT},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if gif:
        embed["image"] = {"url": gif}
    return content, [embed]


@dataclass
class PlacementMember:
    """Un joueur d'un duo qui annonce la fin de ses placements."""

    name: str
    rank_label: str
    tier: str | None = None
    placed: bool = False  # il sortait de placements (sinon : déjà classé)
    wins: int = 0  # bilan des parties de placement
    losses: int = 0


def build_placements_announcement(
    team_name: str,
    team_color: str | None,
    members: Sequence[PlacementMember],
    *,
    average_rank: str | None = None,
    position: int | None = None,
    teams_count: int | None = None,
    lp_net: int | None = None,
    settings: Settings | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Annonce « 🎖️ Duo Rouge a fini ses placements » : rang de chacun, bilan, rang moyen, place."""
    settings = settings if settings is not None else get_settings()
    names = " & ".join(m.name for m in members)
    content = f"🎖️ **{team_name}** ({names}) a fini ses placements !"
    fields = []
    for member in members:
        record = f"\nPlacements : {member.wins} V – {member.losses} D" if member.placed and (member.wins or member.losses) else ""
        fields.append({"name": member.name, "value": f"**{member.rank_label}**{record}", "inline": True})
    if average_rank:
        fields.append({"name": "Rang moyen du duo", "value": average_rank, "inline": True})
    if position and teams_count:
        lp = f" · {format_lp_delta(lp_net)}" if lp_net is not None else ""
        fields.append({"name": "Classement", "value": f"{_ordinal(position)} sur {teams_count}{lp}", "inline": True})
    link = share_link("/dashboard", settings)
    fields.append({"name": "Liens", "value": f"[🏆 Voir le classement]({link})", "inline": False})
    best = max(members, key=lambda m: 0 if m.tier is None else 1)
    embed: dict[str, Any] = {
        "title": f"🎖️ {team_name} a fini ses placements",
        "url": link,
        "description": "Les rangs sont tombés : les LP comptent maintenant à partir de ces rangs.",
        "color": int(team_color.lstrip("#"), 16) if team_color and len(team_color.lstrip("#")) == 6 else COLOR_INFO,
        "fields": fields,
        "footer": {"text": f"{FOOTER_TEXT} · {team_name}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    emblem = ddragon.rank_emblem_url(best.tier) if best.tier else None
    if emblem:
        embed["thumbnail"] = {"url": emblem}
    return content, [embed]


TEST_NOTIFICATION_CONTENT = "🔔 Test de notification — Pékin Express LoL : le webhook Discord fonctionne."


async def build_test_message(
    *, settings: Settings | None = None, client: httpx.AsyncClient | None = None
) -> tuple[str, list[dict[str, Any]]]:
    """Message de test : exemple de carte de résultat + un GIF de victoire et un GIF de défaite."""
    from app.db.models import MatchParticipant, Player, Queue, Team  # import local : évite un cycle
    from app.services import gifs  # import local : seul usage

    settings = settings if settings is not None else get_settings()
    now = datetime.now(timezone.utc)
    player = Player(id=0, display_name="Exemple", game_name="Exemple", tag_line="EUW", profile_icon_id=29)
    team = Team(id=0, name="Duo Exemple", color="#22c55e", slot=1)
    participant = MatchParticipant(
        match_id="EUW1_0",
        player_id=0,
        queue=Queue.SOLO,
        game_start=now,
        game_end=now,
        game_duration=1947,
        champion_name="MonkeyKing",
        win=True,
        kills=15,
        deaths=4,
        assists=15,
        cs=234,
        damage_to_champions=35512,
        vision_score=47,
        kill_participation=68.2,
    )
    highlights = {
        "duration_s": 1947,
        "score": 9.75,
        "badge": "MVP",
        "place": 1,
        "players_count": 10,
        "pings": 53,
        "team_luck": "Très bonne",
        "gold_diff_lane": 1250,
        "largest_multi_kill": 3,
    }
    notice = MatchNotice(
        player=player, team=team, participant=participant, lp_change=19, rank_label="Diamond III · 38 LP",
        highlights=highlights, day_number=4, day_limit=settings.games_per_day,
    )
    sample = match_embed(notice, settings=settings)
    sample["title"] = f"Exemple — {sample['title']}"
    embeds = [sample]
    for win in (True, False):
        choice = await gifs.find_gif(win, settings=settings, client=client)
        label = "victoire" if win else "défaite"
        if choice is None:
            embeds.append(
                {
                    "title": f"GIF de {label} : aucun",
                    "description": "Ajoute ta clé Klipy (KLIPY_API_KEY) dans .env, puis « Recharger .env ».",
                    "color": COLOR_WIN if win else COLOR_LOSS,
                }
            )
            continue
        source = f"catégorie Klipy « {choice.query} »" if choice.query else "lien de secours"
        embeds.append(
            {"title": f"GIF de {label} ({source})", "color": COLOR_WIN if win else COLOR_LOSS, "image": {"url": choice.url}}
        )
    # GIF de l'annonce du début du challenge (lien choisi par l'organisateur)
    if settings.gif_start:
        start_gif = await gifs.resolve_gif(settings.gif_start, settings=settings, client=client)
        if start_gif:
            embeds.append({"title": "GIF de l'annonce du début", "color": COLOR_INFO, "image": {"url": start_gif}})
        else:
            embeds.append(
                {
                    "title": "GIF de l'annonce du début : lien direct introuvable",
                    "description": f"Le message du début contiendra le lien {settings.gif_start} (Discord affiche le GIF dessous).",
                    "color": COLOR_INFO,
                }
            )
    return TEST_NOTIFICATION_CONTENT, embeds
