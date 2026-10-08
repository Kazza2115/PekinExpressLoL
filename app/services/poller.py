"""Poller : cycle périodique Riot → base de données + événements.

Ordre d'un cycle (regroupé par endpoint pour ménager les rate limits) :
  A. League-V4 pour tous les joueurs → `RankSnapshot` si le rang a changé, `rank_changed` ;
  B. Match-V5 pour tous → `Match` + `MatchParticipant` (challenge `running` uniquement),
     `match_recorded` + Discord ; `lp_change` calculé par diff de snapshots ;
  C. Spectator-V5 une fois par joueur → `state.live_games`, `live_start` / `live_end` + Discord.
Chaque étape par joueur est isolée : une erreur est consignée dans `PollReport.errors`
et loggée, jamais propagée. Un seul cycle à la fois (verrou asyncio) ; un appel
concurrent à `poll_once()` attend le cycle en cours et renvoie son rapport.
"""

from __future__ import annotations

import asyncio
import httpx
import json
import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, col, select

from app.config import Settings, get_settings
from app.db.models import (
    QUEUE_BY_ID,
    QUEUE_IDS,
    QUEUE_TYPES,
    REMAKE_MAX_DURATION_S,
    Challenge,
    ChallengeStatus,
    Match,
    MatchParticipant,
    Player,
    Queue,
    RankSnapshot,
    Team,
    game_end_of,
)
from app.db.session import as_utc, session_scope
from app.events import EventBus
from app.riot import ddragon
from app.riot.base import (
    LeagueEntryDTO,
    RiotAPI,
    RiotError,
    RiotNotFound,
    RiotRateLimited,
    RiotUnauthorized,
    RiotUnreachable,
)
from app.services.notifications import format_live_start, format_match_recorded, send_discord
from app.services.stats import absolute_lp
from app.state import AppState, LiveGameState, PollReport

log = logging.getLogger(__name__)

# Files classées (420 solo, 440 flex) : les seules « ranked » pour le challenge
RANKED_QUEUE_IDS = frozenset(QUEUE_IDS.values())
# Tiers sans division : `rank` stocké à None (cf. models.RankSnapshot)
APEX_TIERS = frozenset({"MASTER", "GRANDMASTER", "CHALLENGER"})
# Une partie terminée depuis plus longtemps n'est plus annoncée sur Discord
# (évite le spam si le challenge démarre avec une date de début dans le passé)
NOTIFY_MAX_AGE = timedelta(minutes=30)
# Préfixe des puuid inventés par le client démo (app/riot/demo.py)
DEMO_PUUID_PREFIX = "demo-"
# Nombre d'IDs de parties demandés par joueur et par file à chaque cycle, et pages max
MATCH_IDS_COUNT = 20
MATCH_IDS_MAX_PAGES = 5
# Garde-fou : au-delà, `gameDuration` est forcément en millisecondes (> 27 h de partie)
DURATION_MS_THRESHOLD = 100_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def public_error_label(exc: Exception) -> str:
    """Libellé d'erreur affichable à tous (le détail complet reste dans les logs)."""
    if isinstance(exc, RiotUnauthorized):
        return "clé Riot invalide ou expirée"
    if isinstance(exc, RiotRateLimited):
        return "API Riot saturée (limite de requêtes)"
    if isinstance(exc, RiotNotFound):
        return "introuvable côté Riot"
    if isinstance(exc, (RiotUnreachable, httpx.HTTPError)):
        # RiotClient convertit toute erreur réseau épuisée en RiotUnreachable (status None) ;
        # une httpx.HTTPError brute ne vient que d'un client tiers
        return "API Riot injoignable"
    if isinstance(exc, RiotError):
        return f"erreur API Riot ({exc.status or '?'})"
    return type(exc).__name__


def _queue_of(value: Any) -> Queue:
    """Normalise une valeur de colonne `queue` (membre d'enum ou chaîne)."""
    return value if isinstance(value, Queue) else Queue(str(value))


def _game_end(participant: MatchParticipant) -> datetime:
    return game_end_of(participant)


def _int_or_none(value: Any) -> int | None:
    """Entier depuis une valeur JSON (bool exclu) ; None si absent ou invalide."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _team_side_of(part: dict[str, Any]) -> int | None:
    """`teamId` Match-V5 d'un participant (100 / 200) ; None si absent ou invalide."""
    side = _int_or_none(part.get("teamId"))
    return side if side in (100, 200) else None


# Emplacements d'objets Match-V5 (6 objets + bibelot)
ITEM_SLOTS = tuple(f"item{i}" for i in range(7))


def _items_json(part: dict[str, Any]) -> str | None:
    """`item0`..`item6` → JSON "[3031,3006,0,0,0,0,3340]" ; None si aucun emplacement n'est renseigné."""
    if not any(slot in part for slot in ITEM_SLOTS):
        return None
    return json.dumps([_int_or_none(part.get(slot)) or 0 for slot in ITEM_SLOTS], separators=(",", ":"))


def _spells_of(part: dict[str, Any]) -> str | None:
    """`summoner1Id,summoner2Id` → "4,14" ; None si absents."""
    first = _int_or_none(part.get("summoner1Id"))
    second = _int_or_none(part.get("summoner2Id"))
    if first is None and second is None:
        return None
    return f"{first or 0},{second or 0}"


def _team_kills(parts: list[dict[str, Any]]) -> dict[int, int]:
    """Kills cumulés par côté (`teamId`) à partir des participants."""
    totals: dict[int, int] = {}
    for part in parts:
        side = _team_side_of(part)
        if side is None:
            continue
        totals[side] = totals.get(side, 0) + (_int_or_none(part.get("kills")) or 0)
    return totals


def _kill_participation(kills: int, assists: int, team_kills: int | None) -> float | None:
    """(K + A) / kills de l'équipe × 100, 1 décimale, borné à 100 ; None si l'équipe n'a aucun kill."""
    if not team_kills or team_kills <= 0:
        return None
    return round(min(100.0, (kills + assists) / team_kills * 100), 1)


def _bool_or_none(value: Any) -> bool | None:
    """Booléen depuis une valeur JSON ; None si absent ou d'un autre type."""
    return value if isinstance(value, bool) else None


def _team_totals(parts: list[dict[str, Any]], key: str) -> dict[int, int]:
    """Somme d'un champ numérique par côté (`teamId`)."""
    totals: dict[int, int] = {}
    for part in parts:
        side = _team_side_of(part)
        if side is None:
            continue
        totals[side] = totals.get(side, 0) + (_int_or_none(part.get(key)) or 0)
    return totals


def _share(value: int | None, team_total: int | None) -> float | None:
    """Part d'un joueur dans le total de son équipe (%, 1 décimale) ; None si le total est nul."""
    if not team_total or team_total <= 0:
        return None
    return round((value or 0) / team_total * 100, 1)


# Champ Match-V5 → colonne `MatchParticipant` (entiers)
_DETAIL_INT_FIELDS: dict[str, str] = {
    "doubleKills": "double_kills",
    "tripleKills": "triple_kills",
    "quadraKills": "quadra_kills",
    "pentaKills": "penta_kills",
    "largestMultiKill": "largest_multi_kill",
    "largestKillingSpree": "largest_killing_spree",
    "turretKills": "turret_kills",
    "inhibitorKills": "inhibitor_kills",
    "dragonKills": "dragon_kills",
    "baronKills": "baron_kills",
    "objectivesStolen": "objectives_stolen",
    "totalDamageTaken": "damage_taken",
    "damageSelfMitigated": "damage_mitigated",
    "totalHeal": "total_heal",
    "totalHealsOnTeammates": "heals_on_teammates",
    "timeCCingOthers": "time_ccing_others",
    "totalTimeSpentDead": "time_spent_dead",
    "wardsPlaced": "wards_placed",
    "wardsKilled": "wards_killed",
    "visionWardsBoughtInGame": "control_wards_bought",
}
# Champ Match-V5 → colonne (booléens)
_DETAIL_BOOL_FIELDS: dict[str, str] = {
    "firstBloodKill": "first_blood_kill",
    "gameEndedInSurrender": "surrendered",
}
# Toutes les colonnes de détail (ordre stable)
DETAIL_COLUMNS: tuple[str, ...] = (
    *_DETAIL_INT_FIELDS.values(),
    *_DETAIL_BOOL_FIELDS.values(),
    "damage_share",
    "gold_share",
)


def participant_details(part: dict[str, Any], all_parts: list[dict[str, Any]]) -> dict[str, Any]:
    """Détails d'un participant Match-V5 → colonnes `MatchParticipant` (profil joueur).

    Fonction pure, partagée par `Poller._store_match` et `bootstrap.backfill_match_details`.
    Chaque clé absente ou invalide du JSON donne None (les stats les traitent comme 0).
    `damage_share` / `gold_share` = part du joueur dans le total de son côté (`teamId`),
    None si le côté est inconnu ou si le total de l'équipe est nul.
    """
    details: dict[str, Any] = {column: _int_or_none(part.get(key)) for key, column in _DETAIL_INT_FIELDS.items()}
    for key, column in _DETAIL_BOOL_FIELDS.items():
        details[column] = _bool_or_none(part.get(key))
    side = _team_side_of(part)
    team_damage = _team_totals(all_parts, "totalDamageDealtToChampions").get(side) if side is not None else None
    team_gold = _team_totals(all_parts, "goldEarned").get(side) if side is not None else None
    details["damage_share"] = _share(_int_or_none(part.get("totalDamageDealtToChampions")), team_damage)
    details["gold_share"] = _share(_int_or_none(part.get("goldEarned")), team_gold)
    return details


@dataclass
class _CycleContext:
    """Données partagées par toutes les étapes d'un cycle."""

    now: datetime
    players_by_puuid: dict[str, Player]
    teams_by_id: dict[int, Team]
    unauthorized: bool = False  # clé Riot refusée : on arrête le cycle (loggé une fois)
    unreachable: bool = False  # Riot injoignable : on arrête le cycle (loggé une fois)

    @property
    def aborted(self) -> bool:
        """Cycle à interrompre : inutile d'enchaîner les requêtes (clé refusée ou Riot injoignable)."""
        return self.unauthorized or self.unreachable


# Signature commune des trois étapes par joueur
_Phase = Callable[[Session, Player, Challenge | None, PollReport, _CycleContext], Awaitable[None]]


# --------------------------------------------------------------------------- #
# Calcul des LP gagnés/perdus par partie
# --------------------------------------------------------------------------- #


def last_snapshot(session: Session, player_id: int, queue: Queue) -> RankSnapshot | None:
    """Dernier `RankSnapshot` du joueur pour une file (None si aucun)."""
    statement = (
        select(RankSnapshot)
        .where(RankSnapshot.player_id == player_id, RankSnapshot.queue == queue)
        .order_by(col(RankSnapshot.captured_at).desc(), col(RankSnapshot.id).desc())
    )
    return session.exec(statement).first()


def backfill_lp_changes(session: Session, player: Player) -> int:
    """Renseigne `lp_change` des participations du joueur encore à None.

    Pour chaque partie (hors remake) : `before` = dernier snapshot de la file pris
    avant ou à la fin de la partie, `after` = premier snapshot pris après. Si les
    deux existent et sont classés, `lp_change = after − before`, sauf si une autre
    partie du joueur (même file) s'est terminée entre les deux snapshots : les LP
    seraient alors mélangés → on laisse None (recalculé à chaque cycle, c'est peu
    coûteux). Renvoie le nombre de lignes mises à jour.
    """
    participants = session.exec(
        select(MatchParticipant)
        .where(MatchParticipant.player_id == player.id, MatchParticipant.is_remake == False)  # noqa: E712
        .order_by(col(MatchParticipant.game_start))
    ).all()
    pending = [mp for mp in participants if mp.lp_change is None]
    if not pending:
        return 0

    snapshots = session.exec(
        select(RankSnapshot)
        .where(RankSnapshot.player_id == player.id)
        .order_by(col(RankSnapshot.captured_at), col(RankSnapshot.id))
    ).all()
    snapshots_by_queue: dict[Queue, list[RankSnapshot]] = defaultdict(list)
    for snapshot in snapshots:
        snapshots_by_queue[_queue_of(snapshot.queue)].append(snapshot)
    # Fins de partie par file (pour détecter deux parties dans un même intervalle)
    ends_by_queue: dict[Queue, list[tuple[int | None, datetime]]] = defaultdict(list)
    for mp in participants:
        ends_by_queue[_queue_of(mp.queue)].append((mp.id, _game_end(mp)))

    updated = 0
    for mp in pending:
        queue = _queue_of(mp.queue)
        game_end = _game_end(mp)
        before: RankSnapshot | None = None
        after: RankSnapshot | None = None
        for snapshot in snapshots_by_queue.get(queue, []):
            if as_utc(snapshot.captured_at) <= game_end:
                before = snapshot
            else:
                after = snapshot
                break
        if before is None or after is None:
            continue  # pas encore de snapshot « après » : on réessaiera au prochain cycle
        if before.absolute_lp is None or after.absolute_lp is None:
            continue  # unranked (placements) : différence non définie
        low, high = as_utc(before.captured_at), as_utc(after.captured_at)
        if any(
            other_id != mp.id and low < other_end <= high for other_id, other_end in ends_by_queue[queue]
        ):
            continue  # deux parties dans le même intervalle de polling : indéterminable
        mp.lp_change = after.absolute_lp - before.absolute_lp
        session.add(mp)
        updated += 1
    if updated:
        session.commit()
    return updated


# --------------------------------------------------------------------------- #
# Poller
# --------------------------------------------------------------------------- #


class Poller:
    def __init__(
        self,
        api: RiotAPI,
        bus: EventBus,
        state: AppState,
        settings: Settings | None = None,
    ):
        self.api = api
        self.bus = bus
        self.state = state
        self._settings = settings
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task[PollReport] | None = None

    @property
    def settings(self) -> Settings:
        """Settings injectées (tests) sinon celles en cache, rechargeables à chaud."""
        return self._settings if self._settings is not None else get_settings()

    # ------------------------------------------------------------------ boucle

    async def run_forever(self) -> None:
        """Boucle infinie : un cycle puis `poll_interval_seconds` de pause. Ne crashe jamais."""
        log.info("Poller démarré (intervalle %ss)", self.settings.poll_interval_seconds)
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — la boucle doit survivre à tout
                log.exception("Cycle de polling en échec")
            # Relu à chaque tour : un rechargement du .env change l'intervalle
            await asyncio.sleep(self.settings.poll_interval_seconds)

    async def poll_once(self) -> PollReport:
        """Exécute un cycle complet. Si un cycle est déjà en cours, attend son rapport."""
        inflight = self._inflight
        if self._lock.locked() and inflight is not None and not inflight.done():
            log.debug("Cycle déjà en cours : on attend son rapport")
            return await asyncio.shield(inflight)
        async with self._lock:
            task = asyncio.create_task(self._run_cycle(), name="poll-cycle")
            self._inflight = task
            try:
                return await task
            finally:
                if self._inflight is task:
                    self._inflight = None

    # ------------------------------------------------------------------ cycle

    async def _run_cycle(self) -> PollReport:
        # Version Data Dragon rafraîchie avant le cycle (cache 1 h, jamais bloquant) : sans ça, les
        # icônes (avatars, champions, objets) restaient figées sur la version de repli tant
        # qu'aucune partie en cours n'avait été détectée — hors mesure de `duration_s` (Riot seul)
        await ddragon.get_version()
        report = PollReport(started_at=_utcnow())
        started = time.monotonic()
        requests_before = getattr(self.api, "request_count", None)
        self.state.polling = True
        try:
            with session_scope() as session:
                challenge = session.exec(select(Challenge).order_by(col(Challenge.id))).first()
                players = self._load_players(session)
                report.players_polled = len(players)
                ctx = _CycleContext(
                    now=_utcnow(),
                    players_by_puuid={p.puuid: p for p in players if p.puuid},
                    teams_by_id={t.id: t for t in session.exec(select(Team)).all() if t.id is not None},
                )
                self._forget_unpolled_live_games(session, players, ctx)
                # A. rangs, B. parties, C. parties en cours — chaque étape pour tous les joueurs
                for name, phase in self._phases():
                    for player in players:
                        if ctx.aborted:
                            break
                        await self._run_phase(name, phase, session, player, challenge, report, ctx)
        except Exception as exc:  # noqa: BLE001 — ex. base indisponible
            log.exception("Cycle de polling interrompu")
            report.errors.append(f"cycle : {public_error_label(exc)}")
        finally:
            report.duration_s = time.monotonic() - started
            requests_after = getattr(self.api, "request_count", None)
            if isinstance(requests_before, int) and isinstance(requests_after, int):
                report.requests = max(0, requests_after - requests_before)
            self.state.last_poll = report
            self.state.poll_count += 1
            self.state.polling = False
        self.bus.publish("poll_done", report.to_dict())
        if report.errors:
            log.info("Cycle terminé avec %d erreur(s) en %.2fs", len(report.errors), report.duration_s)
        else:
            log.debug(
                "Cycle terminé : %d joueur(s), %d snapshot(s), %d partie(s), %.2fs",
                report.players_polled,
                report.new_snapshots,
                report.new_matches,
                report.duration_s,
            )
        return report

    async def poll_player(
        self,
        session: Session,
        player: Player,
        challenge: Challenge | None,
        report: PollReport,
        ctx: _CycleContext | None = None,
    ) -> None:
        """Les trois étapes pour un seul joueur (rangs → parties → spectator). Ne lève jamais."""
        if ctx is None:
            ctx = self._build_context(session)
        for name, phase in self._phases():
            if ctx.aborted:
                break
            await self._run_phase(name, phase, session, player, challenge, report, ctx)

    def _phases(self) -> list[tuple[str, _Phase]]:
        return [
            ("league", self._poll_league),
            ("matches", self._poll_matches),
            ("spectator", self._poll_spectator),
        ]

    async def _run_phase(
        self,
        name: str,
        phase: _Phase,
        session: Session,
        player: Player,
        challenge: Challenge | None,
        report: PollReport,
        ctx: _CycleContext,
    ) -> None:
        """Exécute une étape pour un joueur en isolant toute erreur."""
        try:
            await phase(session, player, challenge, report, ctx)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            self._record_error(name, player, exc, report, ctx)

    def _record_error(
        self, phase: str, player: Player, exc: Exception, report: PollReport, ctx: _CycleContext
    ) -> None:
        # Le rapport est public (/health, /api/state, SSE) : libellé court, sans détail interne
        report.errors.append(f"{player.display_name} [{phase}] : {public_error_label(exc)}")
        if isinstance(exc, RiotUnauthorized):
            if not ctx.unauthorized:
                log.error("Clé Riot invalide ou expirée — cycle interrompu (%s)", exc)
            ctx.unauthorized = True
        elif isinstance(exc, RiotUnreachable):
            # Chaque requête a déjà épuisé ses retries : on n'enchaîne pas les suivantes
            if not ctx.unreachable:
                log.warning("API Riot injoignable — cycle interrompu (%s)", exc)
            ctx.unreachable = True
        else:
            # Erreurs Riot et réseau : une ligne suffit ; traceback seulement pour l'inattendu
            expected = isinstance(exc, (RiotError, httpx.HTTPError))
            log.warning("Poll %s — %s : %s", phase, player.display_name, exc, exc_info=not expected)

    # ------------------------------------------------------------------ chargement

    def _load_players(self, session: Session) -> list[Player]:
        """Joueurs actifs ET liés (puuid connu), dans l'ordre d'inscription.

        En mode réel, les joueurs créés en mode démo (puuid `demo-…`, identifiants inventés)
        sont ignorés : Riot répondrait 400 à chaque appel. L'Admin propose de les supprimer.
        """
        statement = (
            select(Player)
            .where(Player.active == True, col(Player.puuid).is_not(None), col(Player.puuid) != "")  # noqa: E712
            .order_by(col(Player.id))
        )
        players = list(session.exec(statement).all())
        if not self.settings.demo_mode:
            demo = [p for p in players if (p.puuid or "").startswith(DEMO_PUUID_PREFIX)]
            if demo:
                log.warning(
                    "%d joueur(s) de démo ignoré(s) en mode réel (%s) : supprime-les dans Admin → Joueurs",
                    len(demo),
                    ", ".join(p.display_name for p in demo),
                )
                players = [p for p in players if p not in demo]
        return players

    def _build_context(self, session: Session) -> _CycleContext:
        players = self._load_players(session)
        return _CycleContext(
            now=_utcnow(),
            players_by_puuid={p.puuid: p for p in players if p.puuid},
            teams_by_id={t.id: t for t in session.exec(select(Team)).all() if t.id is not None},
        )

    def _tracked_queues(self, challenge: Challenge | None) -> list[Queue]:
        queues = [Queue.SOLO]
        if (challenge is not None and challenge.track_flex) or self.settings.track_flex:
            queues.append(Queue.FLEX)
        return queues

    def _team_of(self, player: Player, ctx: _CycleContext) -> Team | None:
        return ctx.teams_by_id.get(player.team_id) if player.team_id is not None else None

    def _forget_unpolled_live_games(
        self, session: Session, players: list[Player], ctx: _CycleContext
    ) -> None:
        """Retire de `live_games` les joueurs désactivés/déliés/supprimés depuis le dernier cycle."""
        polled_ids = {p.id for p in players}
        for player_id in [pid for pid in self.state.live_games if pid not in polled_ids]:
            live = self.state.live_games.pop(player_id)
            player = session.get(Player, player_id)
            self._publish_live_end(player, player_id, live, ctx)

    # ------------------------------------------------------------------ A. League-V4

    async def _poll_league(
        self,
        session: Session,
        player: Player,
        challenge: Challenge | None,
        report: PollReport,
        ctx: _CycleContext,
    ) -> None:
        entries = await self.api.get_league_entries_by_puuid(player.puuid)
        by_type = {entry.queue_type: entry for entry in entries}
        for queue in self._tracked_queues(challenge):
            previous = last_snapshot(session, player.id, queue)
            snapshot = self._build_snapshot(player, queue, by_type.get(QUEUE_TYPES[queue]), previous)
            if snapshot is None:
                continue
            session.add(snapshot)
            session.commit()
            session.refresh(snapshot)
            report.new_snapshots += 1
            previous_abs = previous.absolute_lp if previous is not None else None
            delta = (
                snapshot.absolute_lp - previous_abs
                if snapshot.absolute_lp is not None and previous_abs is not None
                else None
            )
            self.bus.publish(
                "rank_changed",
                {
                    "player_id": player.id,
                    "display_name": player.display_name,
                    "team_id": player.team_id,
                    "queue": queue.value,
                    "tier": snapshot.tier,
                    "rank": snapshot.rank,
                    "lp": snapshot.lp,
                    "wins": snapshot.wins,
                    "losses": snapshot.losses,
                    "absolute_lp": snapshot.absolute_lp,
                    "previous_absolute_lp": previous_abs,
                    "delta": delta,
                    "captured_at": as_utc(snapshot.captured_at).isoformat(),
                },
            )

    @staticmethod
    def _build_snapshot(
        player: Player,
        queue: Queue,
        entry: LeagueEntryDTO | None,
        previous: RankSnapshot | None,
    ) -> RankSnapshot | None:
        """Nouveau snapshot si le rang a changé (ou premier snapshot de référence), sinon None."""
        if entry is None:
            # Unranked : un seul snapshot « tier None », tant que le joueur reste unranked
            if previous is not None and previous.tier is None:
                return None
            return RankSnapshot(
                player_id=player.id,
                queue=queue,
                tier=None,
                rank=None,
                lp=0,
                wins=0,
                losses=0,
                hot_streak=False,
                absolute_lp=None,
                captured_at=_utcnow(),
            )
        tier = (entry.tier or "").upper() or None
        rank = None if (tier in APEX_TIERS or not entry.rank) else entry.rank.upper()
        lp = int(entry.league_points or 0)
        wins, losses = int(entry.wins or 0), int(entry.losses or 0)
        if previous is not None and (previous.tier, previous.rank, previous.lp, previous.wins, previous.losses) == (
            tier,
            rank,
            lp,
            wins,
            losses,
        ):
            return None
        return RankSnapshot(
            player_id=player.id,
            queue=queue,
            tier=tier,
            rank=rank,
            lp=lp,
            wins=wins,
            losses=losses,
            hot_streak=bool(entry.hot_streak),
            absolute_lp=absolute_lp(tier, rank, lp),
            captured_at=_utcnow(),
        )

    # ------------------------------------------------------------------ B. Match-V5

    async def _poll_matches(
        self,
        session: Session,
        player: Player,
        challenge: Challenge | None,
        report: PollReport,
        ctx: _CycleContext,
    ) -> None:
        # Les parties ne sont stockées que pendant le challenge (`running`)
        if challenge is None or challenge.status != ChallengeStatus.RUNNING:
            return
        start_at = as_utc(challenge.start_at)
        if start_at is None:
            return
        start_time = int(start_at.timestamp())

        match_ids: list[str] = []
        for queue in self._tracked_queues(challenge):
            fetched = await self._fetch_match_ids(session, player, QUEUE_IDS[queue], start_time)
            match_ids.extend(match_id for match_id in fetched if match_id not in match_ids)

        # Le statut peut avoir changé pendant le cycle (reset / fin) : on relit la base
        # avant d'écrire, pour ne pas réinsérer des parties juste purgées.
        session.expire(challenge)
        if challenge.status != ChallengeStatus.RUNNING:
            return

        recorded: list[tuple[Player, MatchParticipant]] = []
        if match_ids:
            existing = set(
                session.exec(select(Match.match_id).where(col(Match.match_id).in_(match_ids))).all()
            )
            # Les IDs arrivent du plus récent au plus ancien : on insère dans l'ordre chronologique
            for match_id in reversed([m for m in match_ids if m not in existing]):
                try:
                    raw = await self.api.get_match(match_id)
                except RiotNotFound:
                    log.warning("Partie %s introuvable (Match-V5) : ignorée", match_id)
                    continue
                recorded.extend(self._store_match(session, match_id, raw, report, ctx))

        # LP par partie : pour ce joueur et pour tout autre joueur du challenge présent dans les parties
        touched: dict[int, Player] = {player.id: player}
        touched.update({p.id: p for p, _ in recorded})
        for touched_player in touched.values():
            backfill_lp_changes(session, touched_player)

        for participant_player, participant in recorded:
            self._publish_match(participant_player, participant)
            if participant.is_remake or ctx.now - _game_end(participant) > NOTIFY_MAX_AGE:
                continue
            team = self._team_of(participant_player, ctx)
            await self._notify(
                format_match_recorded(participant_player, team, participant, participant.lp_change)
            )

    async def _fetch_match_ids(
        self, session: Session, player: Player, queue_id: int, start_time: int
    ) -> list[str]:
        """IDs Match-V5 depuis `start_time`, en paginant tant que tout est inconnu.

        Après une coupure du serveur, un joueur peut avoir plus de `MATCH_IDS_COUNT`
        parties non enregistrées : on avance par pages (`start`) jusqu'à retrouver un
        ID déjà en base, une page incomplète, ou `MATCH_IDS_MAX_PAGES`.
        """
        ids: list[str] = []
        for page in range(MATCH_IDS_MAX_PAGES):
            fetched = await self.api.get_match_ids_by_puuid(
                player.puuid, queue_id, start_time=start_time, count=MATCH_IDS_COUNT, start=page * MATCH_IDS_COUNT
            )
            ids.extend(match_id for match_id in fetched if match_id not in ids)
            if len(fetched) < MATCH_IDS_COUNT:
                break
            known = session.exec(select(Match.match_id).where(col(Match.match_id).in_(fetched))).first()
            if known is not None:
                break  # la page contient déjà une partie connue : les suivantes le sont aussi
        return ids

    def _store_match(
        self,
        session: Session,
        match_id: str,
        raw: dict[str, Any],
        report: PollReport,
        ctx: _CycleContext,
    ) -> list[tuple[Player, MatchParticipant]]:
        """Insère `Match` + une `MatchParticipant` par joueur du challenge présent dans la partie."""
        info = raw.get("info") if isinstance(raw, dict) else None
        if not isinstance(info, dict):
            log.warning("Partie %s : JSON Match-V5 sans `info`, ignorée", match_id)
            return []
        if session.get(Match, match_id) is not None:
            return []  # insérée entre-temps (ex. appel direct à poll_player)

        game_start_ms = int(info.get("gameStartTimestamp") or info.get("gameCreation") or 0)
        game_start = datetime.fromtimestamp(game_start_ms / 1000, tz=timezone.utc)
        duration = int(info.get("gameDuration") or 0)
        # Règle Riot : millisecondes si `gameEndTimestamp` est absent (parties < patch 11.20), sinon secondes
        if "gameEndTimestamp" not in info or duration > DURATION_MS_THRESHOLD:
            duration //= 1000
        game_end_ms = int(info.get("gameEndTimestamp") or 0)
        game_end = (
            datetime.fromtimestamp(game_end_ms / 1000, tz=timezone.utc)
            if game_end_ms > 0
            else game_start + timedelta(seconds=duration)
        )
        queue_id = int(info.get("queueId") or 0)
        queue = QUEUE_BY_ID.get(queue_id, Queue.SOLO)
        is_remake = duration < REMAKE_MAX_DURATION_S

        session.add(
            Match(
                match_id=match_id,
                queue_id=queue_id,
                game_start=game_start,
                game_end=game_end,
                game_duration=duration,
                raw_json=json.dumps(raw, ensure_ascii=False, separators=(",", ":")),
            )
        )
        rows: list[tuple[Player, MatchParticipant]] = []
        all_parts = [part for part in info.get("participants") or [] if isinstance(part, dict)]
        team_kills = _team_kills(all_parts)
        for part in all_parts:
            participant_player = ctx.players_by_puuid.get(part.get("puuid") or "")
            if participant_player is None:
                continue
            if self._participant_exists(session, match_id, participant_player.id):
                continue
            champion_id = part.get("championId")
            kills = int(part.get("kills") or 0)
            assists = int(part.get("assists") or 0)
            side = _team_side_of(part)
            champ_level = part.get("champLevel")
            participant = MatchParticipant(
                match_id=match_id,
                player_id=participant_player.id,
                queue=queue,
                game_start=game_start,
                game_end=game_end,
                game_duration=duration,
                is_remake=is_remake,
                champion_name=part.get("championName") or f"Champion {champion_id}",
                champion_id=int(champion_id) if champion_id is not None else None,
                position=part.get("teamPosition") or None,
                team_side=side,
                win=bool(part.get("win")),
                kills=kills,
                deaths=int(part.get("deaths") or 0),
                assists=assists,
                cs=int(part.get("totalMinionsKilled") or 0) + int(part.get("neutralMinionsKilled") or 0),
                gold=int(part.get("goldEarned") or 0),
                damage_to_champions=int(part.get("totalDamageDealtToChampions") or 0),
                vision_score=int(part.get("visionScore") or 0),
                lp_change=None,
                items=_items_json(part),
                spells=_spells_of(part),
                champ_level=int(champ_level) if isinstance(champ_level, int) and not isinstance(champ_level, bool) else None,
                kill_participation=_kill_participation(kills, assists, team_kills.get(side) if side is not None else None),
                **participant_details(part, all_parts),
            )
            session.add(participant)
            rows.append((participant_player, participant))
        session.commit()
        report.new_matches += 1
        return rows

    @staticmethod
    def _participant_exists(session: Session, match_id: str, player_id: int) -> bool:
        statement = select(MatchParticipant.id).where(
            MatchParticipant.match_id == match_id, MatchParticipant.player_id == player_id
        )
        return session.exec(statement).first() is not None

    def _publish_match(self, player: Player, participant: MatchParticipant) -> None:
        self.bus.publish(
            "match_recorded",
            {
                "match_id": participant.match_id,
                "player_id": player.id,
                "display_name": player.display_name,
                "team_id": player.team_id,
                "queue": _queue_of(participant.queue).value,
                "champion_name": participant.champion_name,
                "champion_id": participant.champion_id,
                "position": participant.position,
                "win": participant.win,
                "kills": participant.kills,
                "deaths": participant.deaths,
                "assists": participant.assists,
                "lp_change": participant.lp_change,
                "game_start": as_utc(participant.game_start).isoformat(),
                "game_end": _game_end(participant).isoformat(),
                "game_duration": participant.game_duration,
                "is_remake": participant.is_remake,
            },
        )

    # ------------------------------------------------------------------ C. Spectator-V5

    async def _poll_spectator(
        self,
        session: Session,
        player: Player,
        challenge: Challenge | None,
        report: PollReport,
        ctx: _CycleContext,
    ) -> None:
        try:
            game = await self.api.get_active_game(player.puuid)
        except RiotNotFound:
            game = None  # 404 = pas en partie
        current = self.state.live_games.get(player.id)

        if game is None:
            if current is not None:
                self.state.live_games.pop(player.id, None)
                self._publish_live_end(player, player.id, current, ctx)
            return

        if current is not None and current.game_id == game.game_id:
            # Même partie : Spectator renvoie gameStartTime = 0 pendant le chargement, on complète
            known_start = as_utc(game.game_start)
            if current.game_start.timestamp() <= 0 and known_start is not None and known_start.timestamp() > 0:
                current.game_start = known_start
            return
        if current is not None:
            # Nouvelle partie sans avoir vu la fin de la précédente
            self._publish_live_end(player, player.id, current, ctx)

        champion_name = game.champion_name or await self._champion_name(game.champion_id)
        game_start = as_utc(game.game_start) or datetime.fromtimestamp(0, tz=timezone.utc)
        live = LiveGameState(
            player_id=player.id,
            game_id=game.game_id,
            champion_id=game.champion_id,
            champion_name=champion_name,
            queue_id=game.queue_id,
            game_mode=game.game_mode,
            game_start=game_start,
            detected_at=ctx.now,
        )
        self.state.live_games[player.id] = live
        ranked = game.queue_id in RANKED_QUEUE_IDS
        self.bus.publish(
            "live_start",
            {
                "player_id": player.id,
                "display_name": player.display_name,
                "team_id": player.team_id,
                "champion_name": champion_name,
                "champion_id": game.champion_id,
                "game_id": game.game_id,
                "queue_id": game.queue_id,
                "game_mode": game.game_mode,
                "game_start": game_start.isoformat(),
                "detected_at": ctx.now.isoformat(),
                "ranked": ranked,
            },
        )
        if ranked:
            await self._notify(format_live_start(player, self._team_of(player, ctx), champion_name))

    @staticmethod
    async def _champion_name(champion_id: int) -> str:
        """Nom du champion via Data Dragon, sinon libellé générique."""
        try:
            name = await ddragon.champion_name_from_id(champion_id)
        except Exception:  # noqa: BLE001 — Data Dragon n'est jamais bloquant
            name = None
        return name or f"Champion {champion_id}"

    def _publish_live_end(
        self, player: Player | None, player_id: int, live: LiveGameState, ctx: _CycleContext
    ) -> None:
        self.bus.publish(
            "live_end",
            {
                "player_id": player_id,
                "display_name": player.display_name if player is not None else f"Joueur {player_id}",
                "team_id": player.team_id if player is not None else None,
                "game_id": live.game_id,
                "champion_name": live.champion_name,
                "queue_id": live.queue_id,
                "ranked": live.queue_id in RANKED_QUEUE_IDS,
                "duration_s": live.elapsed_seconds(ctx.now),
            },
        )

    # ------------------------------------------------------------------ Discord

    async def _notify(self, content: str) -> None:
        """Envoi Discord sans jamais propager d'erreur (no-op si webhook non configuré)."""
        try:
            await send_discord(content, settings=self._settings)
        except Exception as exc:  # noqa: BLE001 — déjà géré dans send_discord, ceinture et bretelles
            log.warning("Notification Discord impossible : %s", exc)
