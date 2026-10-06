"""Calculs purs sur les rangs, séries et statistiques des joueurs / duos.

Aucun accès à la base ni au réseau : tout est calculé à partir d'objets déjà
chargés (`Player`, `RankSnapshot`, `MatchParticipant`, `Team`, `LiveGameState`).
Les datetimes lus depuis SQLite sont naïfs → systématiquement passés par `as_utc()`.
"""

from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, tzinfo

from app.db.models import MatchParticipant, Player, Queue, RankSnapshot, Team
from app.db.session import as_utc
from app.state import LiveGameState

# Tiers dans l'ordre croissant ; chaque tier non‑apex vaut 4 divisions × 100 LP = 400 LP
TIERS = [
    "IRON",
    "BRONZE",
    "SILVER",
    "GOLD",
    "PLATINUM",
    "EMERALD",
    "DIAMOND",
    "MASTER",
    "GRANDMASTER",
    "CHALLENGER",
]
# Divisions dans l'ordre croissant (IV = la plus basse)
DIVISIONS = ["IV", "III", "II", "I"]
# Tiers « apex » : pas de division, base commune
APEX_TIERS = {"MASTER", "GRANDMASTER", "CHALLENGER"}
# Base absolue des tiers apex : Diamond I 100 LP → Master 0 LP
APEX_BASE = 7 * 400  # 2800

RANK_COLORS: dict[str, str] = {
    "IRON": "#8a8a8a",
    "BRONZE": "#b07a4a",
    "SILVER": "#a9b4c0",
    "GOLD": "#e5b64d",
    "PLATINUM": "#4fb8a8",
    "EMERALD": "#3fbf7f",
    "DIAMOND": "#5aa0ff",
    "MASTER": "#b465f0",
    "GRANDMASTER": "#f05a5a",
    "CHALLENGER": "#7fe0ff",
    "UNRANKED": "#6b7280",
}


# ---------------------------------------------------------------------------
# Rangs
# ---------------------------------------------------------------------------


def _norm_tier(tier: str | None) -> str | None:
    """Normalise un tier ("gold" → "GOLD") ; None / "UNRANKED" / inconnu → None."""
    if tier is None:
        return None
    upper = tier.strip().upper()
    if upper == "" or upper == "UNRANKED" or upper not in TIERS:
        return None
    return upper


def absolute_lp(tier: str | None, rank: str | None, lp: int) -> int | None:
    """Valeur absolue d'un rang : Iron IV 0 LP = 0, chaque division = 100 LP.

    Master / Grandmaster / Challenger partagent la base 2800 (+ LP).
    Unranked (tier None ou "UNRANKED") → None. Insensible à la casse.
    """
    tier_norm = _norm_tier(tier)
    if tier_norm is None:
        return None
    if tier_norm in APEX_TIERS:
        return APEX_BASE + int(lp)
    tier_index = TIERS.index(tier_norm)
    rank_norm = (rank or "").strip().upper()
    # Division absente/inconnue pour un tier non‑apex : on se cale sur la plus basse (IV)
    division_index = DIVISIONS.index(rank_norm) if rank_norm in DIVISIONS else 0
    return tier_index * 400 + division_index * 100 + int(lp)


def rank_from_absolute_lp(value: int) -> tuple[str, str | None, int]:
    """Inverse d'`absolute_lp`. ≥ 2800 → ("MASTER", None, lp) ; négatif → Iron IV 0 LP."""
    value = max(0, int(value))
    if value >= APEX_BASE:
        return ("MASTER", None, value - APEX_BASE)
    tier_index, remainder = divmod(value, 400)
    division_index, lp = divmod(remainder, 100)
    return (TIERS[tier_index], DIVISIONS[division_index], lp)


def format_rank(tier: str | None, rank: str | None, lp: int) -> str:
    """Libellé lisible : "Gold II · 45 LP", "Master · 120 LP", "Unranked"."""
    tier_norm = _norm_tier(tier)
    if tier_norm is None:
        return "Unranked"
    label = tier_norm.title()
    rank_norm = (rank or "").strip().upper()
    if tier_norm not in APEX_TIERS and rank_norm in DIVISIONS:
        label = f"{label} {rank_norm}"
    return f"{label} · {int(lp)} LP"


def rank_color(tier: str | None) -> str:
    """Couleur hex associée au tier (Unranked si None / inconnu)."""
    tier_norm = _norm_tier(tier)
    return RANK_COLORS[tier_norm or "UNRANKED"]


# ---------------------------------------------------------------------------
# Séries, KDA, winrate, journées
# ---------------------------------------------------------------------------


@dataclass
class StreakInfo:
    kind: str | None  # "W" | "L" | None
    length: int
    best_win: int
    best_loss: int

    @property
    def label(self) -> str:
        """"W3", "L2" ou "—" sans partie."""
        if self.kind is None or self.length == 0:
            return "—"
        return f"{self.kind}{self.length}"


def compute_streak(results: list[bool]) -> StreakInfo:
    """Série en cours (fin de la liste) et meilleures séries ; `results` chronologique."""
    current_kind: str | None = None
    current_len = 0
    best_win = 0
    best_loss = 0
    for won in results:
        kind = "W" if won else "L"
        if kind == current_kind:
            current_len += 1
        else:
            current_kind = kind
            current_len = 1
        if kind == "W":
            best_win = max(best_win, current_len)
        else:
            best_loss = max(best_loss, current_len)
    return StreakInfo(kind=current_kind, length=current_len, best_win=best_win, best_loss=best_loss)


def kda(kills: int, deaths: int, assists: int) -> float:
    """(K + A) / D ; sans mort → K + A ("perfect KDA")."""
    if deaths <= 0:
        return float(kills + assists)
    return (kills + assists) / deaths


def winrate(wins: int, losses: int) -> float | None:
    """Pourcentage de victoires (0..100, 1 décimale) ; None sans partie."""
    total = wins + losses
    if total <= 0:
        return None
    return round(wins / total * 100, 1)


def day_key(dt: datetime, tz: tzinfo) -> str:
    """Clé de journée "YYYY-MM-DD" dans le fuseau `tz` (datetime naïf = UTC)."""
    aware = as_utc(dt)
    assert aware is not None
    return aware.astimezone(tz).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Statistiques joueur
# ---------------------------------------------------------------------------


@dataclass
class PlayerStats:
    player_id: int
    display_name: str
    riot_id: str | None
    is_linked: bool
    active: bool
    team_id: int | None
    profile_icon_id: int | None
    icon_url: str | None
    tier: str | None
    rank: str | None
    lp: int
    rank_label: str
    rank_color: str
    absolute_lp: int | None
    baseline_absolute_lp: int | None
    lp_net: int
    games: int
    wins: int
    losses: int
    winrate: float | None
    games_today: int
    games_per_day: dict[str, int]
    games_limit: int  # = Challenge.games_per_day
    streak: str
    best_win_streak: int
    best_loss_streak: int
    hot_streak: bool
    avg_kda: float | None
    avg_cs_per_min: float | None
    avg_vision: float | None
    avg_damage: float | None
    top_champion: str | None
    top_champion_games: int
    top_champion_winrate: float | None
    live: dict | None  # {"champion_name","champion_icon_url","game_start","elapsed_s","queue_id","game_mode"}
    last_game_at: str | None

    def to_dict(self) -> dict:
        """Types JSON uniquement (les dates sont déjà des chaînes ISO‑8601)."""
        return asdict(self)


def _ddragon_module():
    """Import paresseux de Data Dragon (évite les cycles ; None si le module manque)."""
    try:
        from app.riot import ddragon
    except ImportError:
        return None
    return ddragon


def _snapshot_absolute_lp(snapshot: RankSnapshot) -> int | None:
    """Valeur absolue recalculée depuis les champs (source de vérité : tier/rank/lp)."""
    return absolute_lp(snapshot.tier, snapshot.rank, snapshot.lp)


def _live_to_dict(live: LiveGameState, now: datetime, version: str | None) -> dict:
    """Partie en cours → dict JSON (icône du champion via Data Dragon si disponible)."""
    raw_start = live.game_start if live.game_start.timestamp() > 0 else live.detected_at
    start = as_utc(raw_start)
    assert start is not None
    icon_url: str | None = None
    ddragon = _ddragon_module()
    if ddragon is not None and version is not None:
        icon_url = ddragon.champion_icon_url(version, live.champion_name)
    return {
        "champion_name": live.champion_name,
        "champion_icon_url": icon_url,
        "game_start": start.isoformat(),
        "elapsed_s": max(0, int((now - start).total_seconds())),
        "queue_id": live.queue_id,
        "game_mode": live.game_mode,
    }


def compute_player_stats(
    *,
    player: Player,
    snapshots: list[RankSnapshot],
    participants: list[MatchParticipant],
    window_start: datetime | None,
    window_end: datetime | None,
    games_limit: int,
    tz: tzinfo,
    now: datetime,
    live: LiveGameState | None = None,
    queue: Queue = Queue.SOLO,
    ddragon_version: str | None = None,
) -> PlayerStats:
    """Stats d'un joueur sur sa fenêtre, pour la file `queue` uniquement.

    - rang affiché = dernier snapshot de la file ;
    - référence = dernier snapshot pris avant ou au début de la fenêtre, sinon le premier après ;
    - LP nets = absolu(dernier snapshot ≤ fin de fenêtre) − absolu(référence), 0 si l'un est Unranked ;
    - parties hors fenêtre et remakes exclus des compteurs, moyennes, séries et champion favori.
    """
    now_utc = as_utc(now)
    assert now_utc is not None
    start_utc = as_utc(window_start)
    end_utc = as_utc(window_end)

    # --- Snapshots de la file, ordre chronologique ---------------------------------
    queue_snapshots = sorted(
        (s for s in snapshots if s.queue == queue),
        key=lambda s: as_utc(s.captured_at),  # type: ignore[arg-type,return-value]
    )
    latest = queue_snapshots[-1] if queue_snapshots else None

    baseline: RankSnapshot | None = None
    if queue_snapshots:
        if start_utc is None:
            baseline = queue_snapshots[0]
        else:
            before = [s for s in queue_snapshots if as_utc(s.captured_at) <= start_utc]  # type: ignore[operator]
            if before:
                baseline = before[-1]
            else:
                baseline = queue_snapshots[0]  # premier snapshot après le début

    # Snapshot de fin : le dernier pris avant la fin de fenêtre (classement figé après `window_end`)
    end_snapshot: RankSnapshot | None = latest
    if end_utc is not None:
        within = [s for s in queue_snapshots if as_utc(s.captured_at) <= end_utc]  # type: ignore[operator]
        end_snapshot = within[-1] if within else None

    tier = latest.tier if latest is not None else None
    rank = latest.rank if latest is not None else None
    lp = latest.lp if latest is not None else 0
    hot_streak = bool(latest.hot_streak) if latest is not None else False
    current_abs = _snapshot_absolute_lp(latest) if latest is not None else None
    baseline_abs = _snapshot_absolute_lp(baseline) if baseline is not None else None
    end_abs = _snapshot_absolute_lp(end_snapshot) if end_snapshot is not None else None
    lp_net = end_abs - baseline_abs if (end_abs is not None and baseline_abs is not None) else 0

    # --- Parties de la file dans la fenêtre, hors remakes ----------------------------
    games_in_window: list[MatchParticipant] = []
    for p in sorted(
        (p for p in participants if p.queue == queue),
        key=lambda p: as_utc(p.game_start),  # type: ignore[arg-type,return-value]
    ):
        if p.is_remake:
            continue
        game_start = as_utc(p.game_start)
        assert game_start is not None
        if start_utc is not None and game_start < start_utc:
            continue
        if end_utc is not None and game_start > end_utc:
            continue
        games_in_window.append(p)

    games = len(games_in_window)
    wins = sum(1 for p in games_in_window if p.win)
    losses = games - wins

    games_per_day: dict[str, int] = {}
    for p in games_in_window:
        key = day_key(p.game_start, tz)
        games_per_day[key] = games_per_day.get(key, 0) + 1
    games_today = games_per_day.get(day_key(now_utc, tz), 0)

    streak = compute_streak([bool(p.win) for p in games_in_window])

    avg_kda: float | None = None
    avg_cs_per_min: float | None = None
    avg_vision: float | None = None
    avg_damage: float | None = None
    if games_in_window:
        avg_kda = round(statistics.fmean(kda(p.kills, p.deaths, p.assists) for p in games_in_window), 2)
        avg_cs_per_min = round(
            statistics.fmean(p.cs / (p.game_duration / 60) if p.game_duration > 0 else 0.0 for p in games_in_window),
            2,
        )
        avg_vision = round(statistics.fmean(p.vision_score for p in games_in_window), 1)
        avg_damage = round(statistics.fmean(p.damage_to_champions for p in games_in_window))

    # Champion le plus joué (égalité → celui joué le plus récemment)
    top_champion: str | None = None
    top_champion_games = 0
    top_champion_winrate: float | None = None
    if games_in_window:
        counts: dict[str, int] = {}
        champ_wins: dict[str, int] = {}
        last_index: dict[str, int] = {}
        for index, p in enumerate(games_in_window):
            counts[p.champion_name] = counts.get(p.champion_name, 0) + 1
            champ_wins[p.champion_name] = champ_wins.get(p.champion_name, 0) + (1 if p.win else 0)
            last_index[p.champion_name] = index
        top_champion = max(counts, key=lambda name: (counts[name], last_index[name]))
        top_champion_games = counts[top_champion]
        top_champion_winrate = winrate(champ_wins[top_champion], top_champion_games - champ_wins[top_champion])

    last_game_at: str | None = None
    if games_in_window:
        last_start = as_utc(games_in_window[-1].game_start)
        assert last_start is not None
        last_game_at = last_start.isoformat()

    # --- Data Dragon (icône de profil, icône du champion en live) --------------------
    ddragon = _ddragon_module()
    version = ddragon_version or (getattr(ddragon, "CURRENT_VERSION", None) if ddragon is not None else None)
    icon_url: str | None = None
    if ddragon is not None and version is not None:
        icon_url = ddragon.profile_icon_url(version, player.profile_icon_id)

    live_dict = _live_to_dict(live, now_utc, version) if live is not None else None

    return PlayerStats(
        player_id=int(player.id or 0),
        display_name=player.display_name,
        riot_id=player.riot_id,
        is_linked=player.is_linked,
        active=bool(player.active),
        team_id=player.team_id,
        profile_icon_id=player.profile_icon_id,
        icon_url=icon_url,
        tier=tier,
        rank=rank,
        lp=int(lp),
        rank_label=format_rank(tier, rank, lp),
        rank_color=rank_color(tier),
        absolute_lp=current_abs,
        baseline_absolute_lp=baseline_abs,
        lp_net=lp_net,
        games=games,
        wins=wins,
        losses=losses,
        winrate=winrate(wins, losses),
        games_today=games_today,
        games_per_day=games_per_day,
        games_limit=int(games_limit),
        streak=streak.label,
        best_win_streak=streak.best_win,
        best_loss_streak=streak.best_loss,
        hot_streak=hot_streak,
        avg_kda=avg_kda,
        avg_cs_per_min=avg_cs_per_min,
        avg_vision=avg_vision,
        avg_damage=avg_damage,
        top_champion=top_champion,
        top_champion_games=top_champion_games,
        top_champion_winrate=top_champion_winrate,
        live=live_dict,
        last_game_at=last_game_at,
    )


# ---------------------------------------------------------------------------
# Statistiques duo + classements
# ---------------------------------------------------------------------------


@dataclass
class TeamStats:
    team_id: int
    name: str
    color: str
    slot: int
    position: int
    window_start: str | None
    window_end: str | None
    lp_net: int
    games: int
    wins: int
    losses: int
    winrate: float | None
    games_today: int
    live_count: int
    players: list[PlayerStats]

    def to_dict(self) -> dict:
        return asdict(self)


def _iso_or_none(dt: datetime | None) -> str | None:
    aware = as_utc(dt)
    return aware.isoformat() if aware is not None else None


def compute_team_stats(team: Team, players: list[PlayerStats]) -> TeamStats:
    """Agrège les stats des joueurs d'un duo ; `position` = 0 (rempli par `rank_teams`)."""
    wins = sum(p.wins for p in players)
    losses = sum(p.losses for p in players)
    return TeamStats(
        team_id=int(team.id or 0),
        name=team.name,
        color=team.color,
        slot=team.slot,
        position=0,
        window_start=_iso_or_none(team.window_start),
        window_end=_iso_or_none(team.window_end),
        lp_net=sum(p.lp_net for p in players),
        games=sum(p.games for p in players),
        wins=wins,
        losses=losses,
        winrate=winrate(wins, losses),
        games_today=sum(p.games_today for p in players),
        live_count=sum(1 for p in players if p.live is not None),
        players=list(players),
    )


def rank_teams(teams: list[TeamStats]) -> list[TeamStats]:
    """Classement : LP nets desc, puis winrate desc (None en dernier), puis parties desc, puis slot."""
    ordered = sorted(
        teams,
        key=lambda t: (
            -t.lp_net,
            t.winrate is None,
            -(t.winrate or 0.0),
            -t.games,
            t.slot,
        ),
    )
    for position, team in enumerate(ordered, start=1):
        team.position = position
    return ordered


_SORT_KEYS = {
    "lp_net": "lp_net",
    "winrate": "winrate",
    "games": "games",
    "kda": "avg_kda",
}


def sort_players(players: list[PlayerStats], key: str = "lp_net") -> list[PlayerStats]:
    """Tri décroissant sur `key` (lp_net | winrate | games | kda) ; None en dernier."""
    attr = _SORT_KEYS.get(key, "lp_net")

    def sort_key(p: PlayerStats) -> tuple:
        value = getattr(p, attr)
        return (value is None, -(value or 0), -p.lp_net, p.display_name.lower())

    return sorted(players, key=sort_key)
