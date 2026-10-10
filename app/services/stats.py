"""Calculs purs sur les rangs, séries et statistiques des joueurs / duos.

Aucun accès à la base ni au réseau : tout est calculé à partir d'objets déjà
chargés (`Player`, `RankSnapshot`, `MatchParticipant`, `Team`, `LiveGameState`).
Les datetimes lus depuis SQLite sont naïfs → systématiquement passés par `as_utc()`.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from app.db.models import MatchParticipant, Player, Queue, RankSnapshot, Team, game_end_of
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
# Après la fin d'une fenêtre, on accepte encore les snapshots pris pendant cette grâce
# (Match-V5 et League-V4 reflètent une partie 1 à 3 min après sa fin)
WINDOW_END_GRACE = timedelta(minutes=10)

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
# Libellés français des tiers (classement des rangs)
TIER_LABELS_FR: dict[str, str] = {
    "IRON": "Fer",
    "BRONZE": "Bronze",
    "SILVER": "Argent",
    "GOLD": "Or",
    "PLATINUM": "Platine",
    "EMERALD": "Émeraude",
    "DIAMOND": "Diamant",
    "MASTER": "Maître",
    "GRANDMASTER": "Grand Maître",
    "CHALLENGER": "Challenger",
    "UNRANKED": "Non classé",
}
UNRANKED_LABEL_FR = "Non classé"


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


def label_from_absolute_lp(value: float | None) -> str:
    """Libellé d'une valeur absolue (inverse d'`absolute_lp`) : "Gold II · 45 LP", "Master · 120 LP",
    "Non classé" pour None. Arrondie à l'entier ; ≥ 2800 → Master (les tiers apex partagent la base)."""
    if value is None:
        return UNRANKED_LABEL_FR
    tier, division, lp = rank_from_absolute_lp(round(value))
    return format_rank(tier, division, lp)


def color_from_absolute_lp(value: float | None) -> str:
    """Couleur du tier correspondant à une valeur absolue (gris « Unranked » pour None)."""
    if value is None:
        return rank_color(None)
    return rank_color(rank_from_absolute_lp(round(value))[0])


def _rank_level(tier: str | None, rank: str | None) -> int | None:
    """Échelon (tier × 4 + division) pour compter promotions / rétrogradations ; None si Unranked."""
    tier_norm = _norm_tier(tier)
    if tier_norm is None:
        return None
    level = TIERS.index(tier_norm) * 4
    if tier_norm not in APEX_TIERS:
        rank_norm = (rank or "").strip().upper()
        level += DIVISIONS.index(rank_norm) if rank_norm in DIVISIONS else 0
    return level


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
class ChampionStats:
    """Bilan d'un joueur sur un champion (fenêtre, hors remakes)."""

    champion_name: str
    image_name: str  # identifiant image Data Dragon ("Wukong" → "MonkeyKing")
    icon_url: str | None
    splash_url: str | None
    loading_url: str | None
    games: int
    wins: int
    losses: int
    winrate: float | None
    avg_kda: float | None
    avg_cs_per_min: float | None
    champion_id: int | None = None
    avg_kills: float | None = None
    avg_deaths: float | None = None
    avg_assists: float | None = None
    avg_damage: int | None = None
    avg_kill_participation: float | None = None
    lp_change: int | None = None  # somme des variations connues ; None si aucune
    last_played: str | None = None  # fin de la dernière partie (ISO‑8601)

    def to_dict(self) -> dict:
        return asdict(self)


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
    live: dict | None  # {"champion_name","champion_icon_url","champion_loading_url","champion_splash_url",…}
    last_game_at: str | None
    # Images (None sans Data Dragon / Unranked / sans partie)
    rank_emblem_url: str | None = None
    rank_crest_url: str | None = None
    top_champion_icon_url: str | None = None
    top_champion_splash_url: str | None = None
    top_champion_loading_url: str | None = None
    # Tous les champions joués dans la fenêtre (hors remakes), parties desc puis winrate desc
    champions: list[dict] = field(default_factory=list)
    # --- Profil détaillé : parties de la fenêtre (hors remakes) ; None sans partie.
    # Les colonnes de détail entières à NULL (parties antérieures à leur ajout) comptent pour 0.
    summoner_level: int | None = None
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    avg_kills: float | None = None
    avg_deaths: float | None = None
    avg_assists: float | None = None
    avg_kill_participation: float | None = None  # sur les parties où elle est connue
    avg_cs: float | None = None
    avg_gold: int | None = None
    avg_gold_per_min: float | None = None
    avg_damage_per_min: int | None = None
    avg_damage_share: float | None = None  # sur les parties où elle est connue
    avg_damage_taken: int | None = None
    avg_heal: int | None = None
    avg_cc_time: int | None = None  # secondes
    avg_time_dead: int | None = None  # secondes
    avg_wards_placed: float | None = None
    avg_wards_killed: float | None = None
    avg_control_wards: float | None = None
    double_kills: int | None = None
    triple_kills: int | None = None
    quadra_kills: int | None = None
    penta_kills: int | None = None
    multikills: int | None = None  # doubles + triples + quadras + pentas
    first_bloods: int | None = None
    largest_killing_spree: int | None = None
    largest_multi_kill: int | None = None
    turret_kills: int | None = None
    dragon_kills: int | None = None
    baron_kills: int | None = None
    objectives_stolen: int | None = None
    surrenders: int | None = None  # parties terminées par un abandon (des deux côtés)
    avg_game_duration: int | None = None  # secondes
    total_time_played: int | None = None  # secondes
    longest_game_s: int | None = None
    shortest_game_s: int | None = None
    # LP
    lp_per_game: float | None = None  # lp_net / parties
    lp_known_games: int | None = None  # parties dont la variation de LP est connue
    avg_lp_win: float | None = None
    avg_lp_loss: float | None = None
    best_lp_gain: int | None = None
    worst_lp_loss: int | None = None
    # Côtés (100 = bleu, 200 = rouge)
    games_blue: int | None = None
    wins_blue: int | None = None
    winrate_blue: float | None = None
    games_red: int | None = None
    wins_red: int | None = None
    winrate_red: float | None = None
    # Répartitions : [{position, label, icon_url, games, wins, losses, winrate, avg_kda}] ;
    # durée (3 tranches) ; jour [{day, label, games, wins, losses, winrate, lp_change, limit}] ;
    # moment de la journée (4 tranches, heure de début dans le fuseau)
    by_position: list[dict] = field(default_factory=list)
    by_duration: list[dict] = field(default_factory=list)
    by_day: list[dict] = field(default_factory=list)
    by_hour: list[dict] = field(default_factory=list)
    # Records : clé (RECORD_KEYS) → {match_id, champion_name, …, value, label, win, game_end, position} | None
    records: dict = field(default_factory=dict)
    # Saison (dernier snapshot de la file) et évolution du rang dans la fenêtre
    season_wins: int | None = None
    season_losses: int | None = None
    season_winrate: float | None = None
    peak_absolute_lp: int | None = None
    peak_rank_label: str | None = None
    low_absolute_lp: int | None = None
    low_rank_label: str | None = None
    promotions: int = 0
    demotions: int = 0
    rank_delta_lp: int | None = None  # rang actuel − référence (None si l'un est inconnu)
    # Coéquipier du duo (rempli par `app.api.leaderboard`) : voir `partner_record`
    partner: dict | None = None
    # Quota quotidien : au-delà de `games_limit` parties terminées dans la journée, une partie
    # ne compte plus (ni LP, ni stats). `lp_net` est déjà diminué de `lp_over_quota`.
    games_over_quota: int = 0
    games_today_over_quota: int = 0
    lp_over_quota: int = 0  # LP gagnés (+) ou perdus (−) dans les parties hors quota, non comptés
    lp_net_all_games: int = 0  # LP nets si toutes les parties comptaient (information)
    lp_over_quota_approx: bool = False  # partage au prorata quand un relevé couvre 2 parties
    over_quota_match_ids: list[str] = field(default_factory=list)
    # LP de parties jouées avant le début ou après la fin, vus dans les relevés des bords (retirés)
    lp_outside_window: int = 0
    # Joker du duo : limite du jour (10, ou 13 avec le joker) et jours où il a servi
    games_limit_today: int = 0
    joker_today: bool = False
    joker_days: list[str] = field(default_factory=list)

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


def _ddragon_url(ddragon, name: str, *args) -> str | None:  # noqa: ANN001
    """Appelle `ddragon.<name>(*args)` ; None si le module ou le helper manque."""
    fn = getattr(ddragon, name, None) if ddragon is not None else None
    if not callable(fn):
        return None
    return fn(*args)


def _snapshot_absolute_lp(snapshot: RankSnapshot) -> int | None:
    """Valeur absolue recalculée depuis les champs (source de vérité : tier/rank/lp)."""
    return absolute_lp(snapshot.tier, snapshot.rank, snapshot.lp)


def _live_to_dict(live: LiveGameState, now: datetime, version: str | None) -> dict:
    """Partie en cours → dict JSON (icône du champion via Data Dragon si disponible)."""
    raw_start = live.game_start if live.game_start.timestamp() > 0 else live.detected_at
    start = as_utc(raw_start)
    assert start is not None
    icon_url: str | None = None
    loading_url: str | None = None
    splash_url: str | None = None
    ddragon = _ddragon_module()
    if ddragon is not None and version is not None:
        icon_url = _ddragon_url(ddragon, "champion_icon_url", version, live.champion_name)
        loading_url = _ddragon_url(ddragon, "champion_loading_url", live.champion_name)
        splash_url = _ddragon_url(ddragon, "champion_splash_url", live.champion_name)
    return {
        "game_id": live.game_id,
        # Nom affiché (« Wukong ») ; les images utilisent l'identifiant Data Dragon (« MonkeyKing »)
        "champion_name": _ddragon_url(ddragon, "champion_display_name", live.champion_name) or live.champion_name,
        "champion_icon_url": icon_url,
        "champion_loading_url": loading_url,
        "champion_splash_url": splash_url,
        "game_start": start.isoformat(),
        "elapsed_s": max(0, int((now - start).total_seconds())),
        "queue_id": live.queue_id,
        "game_mode": live.game_mode,
    }


# ---------------------------------------------------------------------------
# Profil joueur : répartitions, records, libellés français
# ---------------------------------------------------------------------------

# `teamPosition` Match-V5 → libellé affiché (ordre d'affichage à égalité de parties)
POSITION_LABELS: dict[str, str] = {
    "TOP": "Top",
    "JUNGLE": "Jungle",
    "MIDDLE": "Mid",
    "BOTTOM": "Bot",
    "UTILITY": "Support",
}
UNKNOWN_POSITION_LABEL = "Inconnu"
# Tranches de durée de partie (secondes) : < 25 min, 25 à 35 min (bornes incluses), > 35 min
DURATION_BUCKETS: tuple[str, ...] = ("< 25 min", "25–35 min", "> 35 min")
DURATION_SHORT_MAX_S = 25 * 60
DURATION_LONG_MIN_S = 35 * 60
# Moments de la journée (heure de début dans le fuseau du challenge) : (libellé, début inclus, fin exclue)
HOUR_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("Matin (6h–12h)", 6, 12),
    ("Après-midi (12h–18h)", 12, 18),
    ("Soirée (18h–24h)", 18, 24),
    ("Nuit (0h–6h)", 0, 6),
)
RECORD_KEYS: tuple[str, ...] = (
    "best_kda",
    "most_kills",
    "most_assists",
    "most_damage",
    "best_cs_per_min",
    "most_vision",
    "biggest_lp_gain",
    "longest_game",
    "shortest_game",
)
# Abréviations françaises (sans dépendre de la locale du système)
FR_WEEKDAYS = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
FR_MONTHS = ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc.")
BLUE_SIDE = 100
RED_SIDE = 200
EMPTY_DISPLAY = "—"


def fr_day_label(day: str) -> str:
    """"2026-10-11" → "dim. 11 oct." (abréviations françaises, indépendant de la locale)."""
    parsed = date.fromisoformat(day)
    return f"{FR_WEEKDAYS[parsed.weekday()]} {parsed.day} {FR_MONTHS[parsed.month - 1]}"


def format_duration(seconds: float | None) -> str:
    """1872 → "31 min 12 s" ; minute ronde → "31 min" ; < 1 min → "45 s" ; None → "—"."""
    if seconds is None:
        return EMPTY_DISPLAY
    total = max(0, round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes == 0:
        return f"{secs} s"
    return f"{minutes} min" if secs == 0 else f"{minutes} min {secs} s"


def format_signed_lp(value: float | None, unit: str = "LP", digits: int = 0) -> str:
    """+28 → "+28 LP", -12 → "-12 LP", 0 → "0 LP" ; None → "—"."""
    if value is None:
        return EMPTY_DISPLAY
    number = f"{abs(value):.{digits}f}" if digits else str(abs(round(value)))
    sign = "" if round(value, digits) == 0 else ("+" if value > 0 else "-")
    return f"{sign}{number} {unit}"


def format_int_fr(value: float) -> str:
    """Entier avec séparateur de milliers français (espace fine insécable) : 32456 → "32 456"."""
    return f"{round(value):,}".replace(",", " ")


def _n(value: int | None) -> int:
    """Colonne de détail entière : NULL compte pour 0."""
    return int(value or 0)


def _round(value: float, digits: int | None) -> float | int:
    return round(value) if digits is None else round(value, digits)


def _per_min(value: float, duration_s: int) -> float:
    return value / (duration_s / 60) if duration_s > 0 else 0.0


def _split_entry(rows: list[MatchParticipant], **extra: Any) -> dict[str, Any]:
    """{…extra, games, wins, losses, winrate} sur un sous-ensemble de parties."""
    wins = sum(1 for p in rows if p.win)
    return {**extra, "games": len(rows), "wins": wins, "losses": len(rows) - wins, "winrate": winrate(wins, len(rows) - wins)}


def _duration_bucket(duration_s: int) -> int:
    if duration_s < DURATION_SHORT_MAX_S:
        return 0
    return 1 if duration_s <= DURATION_LONG_MIN_S else 2


def _hour_bucket(game_start: datetime, tz: tzinfo) -> int:
    start = as_utc(game_start)
    assert start is not None
    hour = start.astimezone(tz).hour
    return next(index for index, (_label, low, high) in enumerate(HOUR_BUCKETS) if low <= hour < high)


def _norm_position(position: str | None) -> str | None:
    upper = (position or "").strip().upper()
    return upper if upper in POSITION_LABELS else None


def _known_sum(values: list[int | None]) -> int | None:
    """Somme des valeurs connues ; None si aucune."""
    known = [int(v) for v in values if v is not None]
    return sum(known) if known else None


def _by_position(games: list[MatchParticipant], ddragon) -> list[dict[str, Any]]:  # noqa: ANN001
    groups: dict[str | None, list[MatchParticipant]] = {}
    for p in games:
        groups.setdefault(_norm_position(p.position), []).append(p)
    order = list(POSITION_LABELS) + [None]
    entries = []
    for position, rows in groups.items():
        entry = _split_entry(
            rows,
            position=position,
            label=POSITION_LABELS.get(position or "", UNKNOWN_POSITION_LABEL),
            icon_url=_ddragon_url(ddragon, "position_icon_url", position) if position else None,
        )
        entry["avg_kda"] = round(statistics.fmean(kda(p.kills, p.deaths, p.assists) for p in rows), 2)
        entries.append(entry)
    entries.sort(key=lambda e: (-e["games"], -e["wins"], order.index(e["position"])))
    return entries


def _by_day(games: list[MatchParticipant], tz: tzinfo, games_limit: int) -> list[dict[str, Any]]:
    groups: dict[str, list[MatchParticipant]] = {}
    for p in games:
        groups.setdefault(day_key(game_end_of(p), tz), []).append(p)
    return [
        {
            **_split_entry(rows, day=day, label=fr_day_label(day)),
            "lp_change": _known_sum([p.lp_change for p in rows]),
            "limit": int(games_limit),
        }
        for day, rows in sorted(groups.items())
    ]


def _pick_record(
    games: list[MatchParticipant], key: Callable[[MatchParticipant], float | None], *, lowest: bool = False
) -> tuple[MatchParticipant, float] | None:
    """Partie au meilleur `key` (max, ou min si `lowest`) ; égalité → la plus récente ; None ignoré."""
    best: tuple[MatchParticipant, float] | None = None
    for p in games:  # ordre chronologique
        value = key(p)
        if value is None:
            continue
        if best is None or value == best[1] or (value < best[1] if lowest else value > best[1]):
            best = (p, value)
    return best


def _records(games: list[MatchParticipant], version: str | None, ddragon) -> dict[str, dict | None]:  # noqa: ANN001
    """Records personnels de la fenêtre (une entrée par clé de `RECORD_KEYS`, None sans partie éligible)."""

    def score(p: MatchParticipant) -> str:
        return f"{p.kills}/{p.deaths}/{p.assists}"

    def kda_label(p: MatchParticipant, value: float) -> str:
        return f"{score(p)} · KDA parfait" if p.deaths == 0 else f"{score(p)} · KDA {value:.1f}"

    specs: dict[str, tuple[Callable[[MatchParticipant], float | None], Callable[[MatchParticipant, float], str], bool]] = {
        "best_kda": (lambda p: round(kda(p.kills, p.deaths, p.assists), 2), kda_label, False),
        "most_kills": (lambda p: p.kills, lambda p, v: f"{p.kills} kills · {score(p)}", False),
        "most_assists": (lambda p: p.assists, lambda p, v: f"{p.assists} assists · {score(p)}", False),
        "most_damage": (lambda p: p.damage_to_champions, lambda p, v: f"{format_int_fr(v)} dégâts", False),
        "best_cs_per_min": (
            lambda p: round(_per_min(p.cs, p.game_duration), 1) if p.game_duration > 0 else None,
            lambda p, v: f"{v:.1f} CS/min · {p.cs} CS",
            False,
        ),
        "most_vision": (lambda p: p.vision_score, lambda p, v: f"Score de vision {round(v)}", False),
        "biggest_lp_gain": (
            lambda p: p.lp_change if p.lp_change is not None and p.lp_change > 0 else None,
            lambda p, v: format_signed_lp(v),
            False,
        ),
        "longest_game": (lambda p: p.game_duration, lambda p, v: format_duration(v), False),
        "shortest_game": (lambda p: p.game_duration, lambda p, v: format_duration(v), True),
    }
    records: dict[str, dict | None] = {}
    for key in RECORD_KEYS:
        value_of, label_of, lowest = specs[key]
        picked = _pick_record(games, value_of, lowest=lowest)
        if picked is None:
            records[key] = None
            continue
        p, value = picked
        records[key] = {
            "match_id": p.match_id,
            "champion_name": p.champion_name,
            "champion_icon_url": _ddragon_url(ddragon, "champion_icon_url", version, p.champion_name)
            if version is not None
            else None,
            "champion_splash_url": _ddragon_url(ddragon, "champion_splash_url", p.champion_name),
            "value": value,
            "label": label_of(p, value),
            "win": bool(p.win),
            "game_end": game_end_of(p).isoformat(),
            "position": p.position,
        }
    return records


def game_profile(
    games: list[MatchParticipant],
    *,
    tz: tzinfo,
    games_limit: int,
    version: str | None = None,
) -> dict[str, Any]:
    """Champs « profil » de `PlayerStats` sur des parties filtrées (fenêtre, file, hors remakes,
    ordre chronologique). Sans partie : compteurs et moyennes None, `by_duration` / `by_hour`
    à zéro (toujours 3 / 4 entrées), `by_position` / `by_day` vides, records à None.
    """
    ddragon = _ddragon_module()
    profile: dict[str, Any] = {
        "by_duration": [
            _split_entry([p for p in games if _duration_bucket(p.game_duration) == index], label=label)
            for index, label in enumerate(DURATION_BUCKETS)
        ],
        "by_hour": [
            _split_entry([p for p in games if _hour_bucket(p.game_start, tz) == index], label=label)
            for index, (label, _low, _high) in enumerate(HOUR_BUCKETS)
        ],
        "by_position": _by_position(games, ddragon),
        "by_day": _by_day(games, tz, games_limit),
        "records": _records(games, version, ddragon),
    }
    if not games:
        return profile

    def total(attr: str) -> int:
        return sum(_n(getattr(p, attr)) for p in games)

    def mean(values: list[float], digits: int | None) -> float | int:
        return _round(statistics.fmean(values), digits)

    def avg(attr: str, digits: int | None) -> float | int:
        return mean([_n(getattr(p, attr)) for p in games], digits)

    durations = [int(p.game_duration) for p in games]
    multikills = {attr: total(attr) for attr in ("double_kills", "triple_kills", "quadra_kills", "penta_kills")}
    known_lp = [p.lp_change for p in games if p.lp_change is not None]
    win_lp = [p.lp_change for p in games if p.win and p.lp_change is not None]
    loss_lp = [p.lp_change for p in games if not p.win and p.lp_change is not None]
    blue = [p for p in games if p.team_side == BLUE_SIDE]
    red = [p for p in games if p.team_side == RED_SIDE]
    blue_wins = sum(1 for p in blue if p.win)
    red_wins = sum(1 for p in red if p.win)
    profile.update(
        kills=total("kills"),
        deaths=total("deaths"),
        assists=total("assists"),
        avg_kills=avg("kills", 1),
        avg_deaths=avg("deaths", 1),
        avg_assists=avg("assists", 1),
        avg_kill_participation=_mean_or_none([p.kill_participation for p in games], 1),
        avg_cs=avg("cs", 1),
        avg_gold=avg("gold", None),
        avg_gold_per_min=mean([_per_min(_n(p.gold), p.game_duration) for p in games], 1),
        avg_damage_per_min=mean([_per_min(_n(p.damage_to_champions), p.game_duration) for p in games], None),
        avg_damage_share=_mean_or_none([p.damage_share for p in games], 1),
        avg_damage_taken=avg("damage_taken", None),
        avg_heal=avg("total_heal", None),
        avg_cc_time=avg("time_ccing_others", None),
        avg_time_dead=avg("time_spent_dead", None),
        avg_wards_placed=avg("wards_placed", 1),
        avg_wards_killed=avg("wards_killed", 1),
        avg_control_wards=avg("control_wards_bought", 1),
        **multikills,
        multikills=sum(multikills.values()),
        first_bloods=sum(1 for p in games if p.first_blood_kill),
        largest_killing_spree=max(_n(p.largest_killing_spree) for p in games),
        largest_multi_kill=max(_n(p.largest_multi_kill) for p in games),
        turret_kills=total("turret_kills"),
        dragon_kills=total("dragon_kills"),
        baron_kills=total("baron_kills"),
        objectives_stolen=total("objectives_stolen"),
        surrenders=sum(1 for p in games if p.surrendered),
        avg_game_duration=round(statistics.fmean(durations)),
        total_time_played=sum(durations),
        longest_game_s=max(durations),
        shortest_game_s=min(durations),
        lp_known_games=len(known_lp),
        avg_lp_win=round(statistics.fmean(win_lp), 1) if win_lp else None,
        avg_lp_loss=round(statistics.fmean(loss_lp), 1) if loss_lp else None,
        best_lp_gain=max((v for v in known_lp if v > 0), default=None),
        worst_lp_loss=min((v for v in known_lp if v < 0), default=None),
        games_blue=len(blue),
        wins_blue=blue_wins,
        winrate_blue=winrate(blue_wins, len(blue) - blue_wins),
        games_red=len(red),
        wins_red=red_wins,
        winrate_red=winrate(red_wins, len(red) - red_wins),
    )
    return profile


def rank_progress(snapshots: list[RankSnapshot]) -> dict[str, Any]:
    """Pic, creux, promotions et rétrogradations sur des snapshots chronologiques d'une file.

    Promotion / rétrogradation = changement de tier ou de division entre deux snapshots classés
    consécutifs (les passages par Unranked sont ignorés).
    """
    ranked = [(s, _snapshot_absolute_lp(s)) for s in snapshots]
    ranked = [(s, value) for s, value in ranked if value is not None]
    result: dict[str, Any] = {
        "peak_absolute_lp": None,
        "peak_rank_label": None,
        "low_absolute_lp": None,
        "low_rank_label": None,
        "promotions": 0,
        "demotions": 0,
    }
    if not ranked:
        return result
    peak = max(ranked, key=lambda item: item[1])
    low = min(ranked, key=lambda item: item[1])
    result.update(
        peak_absolute_lp=peak[1],
        peak_rank_label=format_rank(peak[0].tier, peak[0].rank, peak[0].lp),
        low_absolute_lp=low[1],
        low_rank_label=format_rank(low[0].tier, low[0].rank, low[0].lp),
    )
    previous: int | None = None
    for snapshot in snapshots:
        level = _rank_level(snapshot.tier, snapshot.rank)
        if level is not None and previous is not None:
            if level > previous:
                result["promotions"] += 1
            elif level < previous:
                result["demotions"] += 1
        previous = level
    return result


def partner_record(
    stats: PlayerStats,
    *,
    partner_id: int,
    display_name: str,
    icon_url: str | None,
    together: tuple[int, int, int],
) -> dict[str, Any]:
    """Bilan avec le coéquipier du duo : parties ensemble (`together_record`) et sans lui (le reste)."""
    games, wins, losses = (int(v) for v in together)
    solo_wins = max(0, stats.wins - wins)
    solo_losses = max(0, stats.losses - losses)
    return {
        "player_id": int(partner_id),
        "display_name": display_name,
        "icon_url": icon_url,
        "together_games": games,
        "together_wins": wins,
        "together_losses": losses,
        "together_winrate": winrate(wins, losses),
        "solo_games": solo_wins + solo_losses,
        "solo_wins": solo_wins,
        "solo_losses": solo_losses,
        "solo_winrate": winrate(solo_wins, solo_losses),
    }


# Jokers d'un joueur : journée "YYYY-MM-DD" → (activé à (UTC), parties en plus)
Jokers = dict[str, tuple[datetime, int]]


def split_daily_quota(
    games: list[MatchParticipant], tz: tzinfo, limit: int, jokers: Jokers | None = None
) -> tuple[list[MatchParticipant], list[MatchParticipant]]:
    """Sépare les parties comptées des parties « hors quota ».

    `games` : parties de la fenêtre (une file, hors remakes). Par journée (fuseau `tz`, date de
    fin de partie, comme le compteur « aujourd'hui x/10 »), seules les `limit` premières parties
    terminées comptent ; les suivantes ne comptent pas. `limit` ≤ 0 : pas de quota.

    Joker du duo ce jour-là (`jokers[jour] = (activé à, n)`) : n parties de plus peuvent compter,
    mais seulement parmi celles terminées **après** l'activation (pas de joker « après coup » sur
    des parties déjà jouées).
    """
    counted: list[MatchParticipant] = []
    over: list[MatchParticipant] = []
    counted_per_day: dict[str, int] = {}
    extra_used: dict[str, int] = {}
    for game in sorted(games, key=game_end_of):
        ended = game_end_of(game)
        key = day_key(ended, tz)
        if limit <= 0 or counted_per_day.get(key, 0) < limit:
            counted.append(game)
            counted_per_day[key] = counted_per_day.get(key, 0) + 1
            continue
        joker = (jokers or {}).get(key)
        if joker is not None and ended >= as_utc(joker[0]) and extra_used.get(key, 0) < joker[1]:  # type: ignore[operator]
            counted.append(game)
            counted_per_day[key] = counted_per_day.get(key, 0) + 1
            extra_used[key] = extra_used.get(key, 0) + 1
            continue
        over.append(game)
    return counted, over


def _games_played(previous: RankSnapshot, current: RankSnapshot) -> int | None:
    """Parties classées jouées entre deux relevés, d'après les compteurs victoires + défaites de Riot."""
    if None in (previous.wins, previous.losses, current.wins, current.losses):
        return None
    return (current.wins + current.losses) - (previous.wins + previous.losses)


def excluded_lp(
    snapshots: list[RankSnapshot],
    games: list[MatchParticipant],
    over: list[MatchParticipant],
    *,
    start: datetime | None = None,
    end: datetime | None = None,
) -> tuple[int, int, bool]:
    """LP à retirer des LP nets : (LP des parties hors quota, LP des parties hors fenêtre, approx).

    `snapshots` : relevés de la file, chronologiques, du relevé de référence au relevé de fin
    inclus. L'écart entre deux relevés consécutifs revient aux parties terminées entre les deux
    (`games` : parties de la fenêtre ; `over` : celles hors quota).

    Aux bords de la fenêtre (intervalle qui contient le début, ou qui finit après la fin), des
    parties jouées avant le début ou après la fin peuvent tomber dans le même écart : les compteurs
    victoires + défaites de Riot disent combien de parties ont été jouées ; celles qui ne sont pas
    des parties de la fenêtre sont hors fenêtre, et leur part de l'écart est retirée.

    Un écart partagé entre parties comptées et non comptées est réparti au prorata du nombre de
    parties : `approx=True`.
    """
    if len(snapshots) < 2:
        return 0, 0, False
    over_keys = {id(game) for game in over}
    start_utc, end_utc = as_utc(start), as_utc(end)
    over_total = 0
    outside_total = 0
    approx = False
    for previous, current in zip(snapshots, snapshots[1:]):
        before = _snapshot_absolute_lp(previous)
        after = _snapshot_absolute_lp(current)
        if before is None or after is None:
            continue
        low, high = as_utc(previous.captured_at), as_utc(current.captured_at)
        if end_utc is not None and low >= end_utc:  # type: ignore[operator]
            # Écart entièrement après la fin (ex. dodge pendant la grace de 10 min) : jamais compté
            outside_total += after - before
            continue
        inside = [game for game in games if low < game_end_of(game) <= high]  # type: ignore[operator]
        n_over = sum(1 for game in inside if id(game) in over_keys)
        n_outside = 0
        at_edge = (start_utc is not None and low < start_utc) or (end_utc is not None and high > end_utc)  # type: ignore[operator]
        if at_edge:
            played = _games_played(previous, current)
            if played is not None and played > len(inside):
                n_outside = played - len(inside)
        n_total = len(inside) + n_outside
        if n_total == 0 or n_over + n_outside == 0:
            continue
        delta = after - before
        if n_over + n_outside == n_total:
            share_over = round(delta * n_over / n_total)
            over_total += share_over
            outside_total += delta - share_over
        else:
            over_total += round(delta * n_over / n_total)
            outside_total += round(delta * n_outside / n_total)
            approx = True
    return over_total, outside_total, approx


def over_quota_lp(
    snapshots: list[RankSnapshot], games: list[MatchParticipant], over: list[MatchParticipant]
) -> tuple[int, bool]:
    """LP gagnés ou perdus pendant les parties hors quota, à retirer des LP nets.

    `snapshots` : relevés de rang de la file, chronologiques, du relevé de référence au relevé de
    fin inclus (ceux qui font les LP nets). Un relevé n'est pris que quand le rang change : l'écart
    entre deux relevés consécutifs revient aux parties terminées entre les deux. Si cet écart mêle
    une partie comptée et une partie hors quota (serveur arrêté entre les deux), il est partagé au
    prorata du nombre de parties : renvoyé avec `approx=True`.
    """
    if not over or len(snapshots) < 2:
        return 0, False
    over_keys = {id(game) for game in over}
    total = 0
    approx = False
    for previous, current in zip(snapshots, snapshots[1:]):
        before = _snapshot_absolute_lp(previous)
        after = _snapshot_absolute_lp(current)
        if before is None or after is None:
            continue
        low, high = as_utc(previous.captured_at), as_utc(current.captured_at)
        inside = [game for game in games if low < game_end_of(game) <= high]  # type: ignore[operator]
        excluded = [game for game in inside if id(game) in over_keys]
        if not excluded:
            continue
        delta = after - before
        if len(excluded) == len(inside):
            total += delta
        else:
            total += round(delta * len(excluded) / len(inside))
            approx = True
    return total, approx


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
    jokers: Jokers | None = None,
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

    # Snapshot de fin : le dernier pris avant la fin de fenêtre (classement figé après `window_end`),
    # avec une courte période de grâce : les résultats Riot arrivent 1 à 3 min après la partie.
    end_snapshot: RankSnapshot | None = latest
    if end_utc is not None:
        limit = end_utc + WINDOW_END_GRACE
        within = [s for s in queue_snapshots if as_utc(s.captured_at) <= limit]  # type: ignore[operator]
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
    # Une partie compte si elle se *termine* dans la fenêtre (les LP sont appliqués à la fin,
    # comme les snapshots de rang) ; les journées (10 games/jour) sont aussi comptées à la fin.
    games_in_window: list[MatchParticipant] = []
    for p in sorted(
        (p for p in participants if p.queue == queue),
        key=game_end_of,
    ):
        if p.is_remake:
            continue
        game_end = game_end_of(p)
        if start_utc is not None and game_end < start_utc:
            continue
        if end_utc is not None and game_end > end_utc:
            continue
        games_in_window.append(p)

    # Quota quotidien : seules les `games_limit` premières parties terminées de chaque journée
    # comptent. Les LP des autres sont retirés des LP nets (attribués relevé par relevé).
    all_games_in_window = games_in_window
    games_in_window, games_over = split_daily_quota(all_games_in_window, tz, int(games_limit), jokers)
    lp_net_all_games = lp_net
    lp_over = 0
    lp_outside = 0
    lp_over_approx = False
    if baseline is not None and end_snapshot is not None and end_abs is not None and baseline_abs is not None:
        start_index = next(i for i, snap in enumerate(queue_snapshots) if snap is baseline)
        end_index = next(i for i, snap in enumerate(queue_snapshots) if snap is end_snapshot)
        if end_index > start_index:
            lp_over, lp_outside, lp_over_approx = excluded_lp(
                queue_snapshots[start_index : end_index + 1],
                all_games_in_window,
                games_over,
                start=start_utc,
                end=end_utc,
            )
    lp_net = lp_net_all_games - lp_over - lp_outside
    over_per_day: dict[str, int] = {}
    for p in games_over:
        key = day_key(game_end_of(p), tz)
        over_per_day[key] = over_per_day.get(key, 0) + 1

    games = len(games_in_window)
    wins = sum(1 for p in games_in_window if p.win)
    losses = games - wins

    games_per_day: dict[str, int] = {}
    for p in games_in_window:
        key = day_key(game_end_of(p), tz)
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
        last_game_at = game_end_of(games_in_window[-1]).isoformat()

    # --- Data Dragon (icône de profil, icône du champion en live, images de rang) -----
    ddragon = _ddragon_module()
    version = ddragon_version or (getattr(ddragon, "CURRENT_VERSION", None) if ddragon is not None else None)
    icon_url: str | None = None
    rank_emblem_url: str | None = None
    rank_crest_url: str | None = None
    top_champion_icon_url: str | None = None
    top_champion_splash_url: str | None = None
    top_champion_loading_url: str | None = None
    if ddragon is not None and version is not None:
        icon_url = _ddragon_url(ddragon, "profile_icon_url", version, player.profile_icon_id)
        rank_emblem_url = _ddragon_url(ddragon, "rank_emblem_url", tier)
        rank_crest_url = _ddragon_url(ddragon, "rank_mini_crest_url", tier)
        if top_champion is not None:
            top_champion_icon_url = _ddragon_url(ddragon, "champion_icon_url", version, top_champion)
            top_champion_splash_url = _ddragon_url(ddragon, "champion_splash_url", top_champion)
            top_champion_loading_url = _ddragon_url(ddragon, "champion_loading_url", top_champion)

    live_dict = _live_to_dict(live, now_utc, version) if live is not None else None
    champions = [c.to_dict() for c in compute_champion_stats(games_in_window, version)]

    # --- Profil détaillé : saison, évolution du rang dans la fenêtre, parties --------
    season_wins: int | None = None
    season_losses: int | None = None
    if latest is not None and current_abs is not None:
        season_wins, season_losses = int(latest.wins), int(latest.losses)
    # Snapshots de la fenêtre (avec sa période de grâce), précédés de la référence
    window_snapshots = [
        s
        for s in queue_snapshots
        if (start_utc is None or as_utc(s.captured_at) >= start_utc)  # type: ignore[operator]
        and (end_utc is None or as_utc(s.captured_at) <= end_utc + WINDOW_END_GRACE)  # type: ignore[operator]
    ]
    if baseline is not None and not any(s is baseline for s in window_snapshots):
        window_snapshots.insert(0, baseline)
    profile = game_profile(games_in_window, tz=tz, games_limit=games_limit, version=version)
    for entry in profile.get("by_day") or []:
        entry["over_quota"] = over_per_day.get(entry["day"], 0)
        joker_of_day = (jokers or {}).get(entry["day"])
        entry["joker"] = joker_of_day is not None
        if joker_of_day is not None:
            entry["limit"] = int(games_limit) + int(joker_of_day[1])
    today_key = day_key(now_utc, tz)
    joker_today = (jokers or {}).get(today_key)

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
        rank_emblem_url=rank_emblem_url,
        rank_crest_url=rank_crest_url,
        top_champion_icon_url=top_champion_icon_url,
        top_champion_splash_url=top_champion_splash_url,
        top_champion_loading_url=top_champion_loading_url,
        champions=champions,
        summoner_level=player.summoner_level,
        lp_per_game=round(lp_net / games, 1) if games else None,
        season_wins=season_wins,
        season_losses=season_losses,
        season_winrate=winrate(season_wins, season_losses) if season_wins is not None and season_losses is not None else None,
        rank_delta_lp=current_abs - baseline_abs if (current_abs is not None and baseline_abs is not None) else None,
        **rank_progress(window_snapshots),
        **profile,
        games_over_quota=len(games_over),
        games_today_over_quota=over_per_day.get(day_key(now_utc, tz), 0),
        lp_over_quota=lp_over,
        lp_net_all_games=lp_net_all_games,
        lp_over_quota_approx=lp_over_approx,
        over_quota_match_ids=[p.match_id for p in games_over],
        lp_outside_window=lp_outside,
        games_limit_today=int(games_limit) + (int(joker_today[1]) if joker_today is not None else 0),
        joker_today=joker_today is not None,
        joker_days=sorted((jokers or {}).keys()),
    )


def compute_champion_stats(games: list[MatchParticipant], version: str | None = None) -> list[ChampionStats]:
    """Bilan par champion sur des parties déjà filtrées (fenêtre, file, hors remakes).

    Tri : parties desc, puis winrate desc (None en dernier), puis nom. Images Data Dragon
    si le module est disponible (sinon None).
    """
    if not games:
        return []
    ddragon = _ddragon_module()
    version = version or (getattr(ddragon, "CURRENT_VERSION", None) if ddragon is not None else None)
    by_champion: dict[str, list[MatchParticipant]] = {}
    for p in games:
        by_champion.setdefault(p.champion_name, []).append(p)
    result: list[ChampionStats] = []
    for name, rows in by_champion.items():
        wins = sum(1 for p in rows if p.win)
        losses = len(rows) - wins
        image_name = _ddragon_url(ddragon, "champion_image_name", name) or name
        icon_url = splash_url = loading_url = None
        if ddragon is not None and version is not None:
            icon_url = _ddragon_url(ddragon, "champion_icon_url", version, name)
            splash_url = _ddragon_url(ddragon, "champion_splash_url", name)
            loading_url = _ddragon_url(ddragon, "champion_loading_url", name)
        result.append(
            ChampionStats(
                champion_name=name,
                image_name=image_name,
                icon_url=icon_url,
                splash_url=splash_url,
                loading_url=loading_url,
                games=len(rows),
                wins=wins,
                losses=losses,
                winrate=winrate(wins, losses),
                avg_kda=round(statistics.fmean(kda(p.kills, p.deaths, p.assists) for p in rows), 2),
                avg_cs_per_min=round(
                    statistics.fmean(p.cs / (p.game_duration / 60) if p.game_duration > 0 else 0.0 for p in rows), 2
                ),
                champion_id=next((p.champion_id for p in reversed(rows) if p.champion_id is not None), None),
                avg_kills=round(statistics.fmean(p.kills for p in rows), 1),
                avg_deaths=round(statistics.fmean(p.deaths for p in rows), 1),
                avg_assists=round(statistics.fmean(p.assists for p in rows), 1),
                avg_damage=round(statistics.fmean(p.damage_to_champions for p in rows)),
                avg_kill_participation=_mean_or_none([p.kill_participation for p in rows], 1),
                lp_change=_known_sum([p.lp_change for p in rows]),
                last_played=max(game_end_of(p) for p in rows).isoformat(),
            )
        )
    result.sort(key=lambda c: (-c.games, c.winrate is None, -(c.winrate or 0.0), c.champion_name.lower()))
    return result


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
    # « Meilleur » des deux joueurs : lp_net, puis winrate (None = plus petit), puis avg_kda, puis games
    mvp_player_id: int | None = None
    # Moyennes des moyennes des joueurs (valeurs None ignorées) ; None si aucun joueur n'a de partie
    avg_kda: float | None = None
    avg_cs_per_min: float | None = None
    avg_vision: float | None = None
    avg_damage: float | None = None
    # Parties jouées ensemble (même partie, même côté, hors remake, dans la fenêtre)
    together_games: int = 0
    together_wins: int = 0
    together_losses: int = 0
    together_winrate: float | None = None
    # Splash du champion favori du MVP (fond de carte) ; None sans MVP / sans partie
    mvp_top_champion_splash_url: str | None = None
    # Rang moyen des joueurs classés (None si aucun) et meilleur rang du duo
    avg_absolute_lp: int | None = None
    rank_label: str = UNRANKED_LABEL_FR
    rank_color: str = RANK_COLORS["UNRANKED"]
    top_player_id: int | None = None
    top_player_rank_label: str | None = None
    # Sommes sur les deux joueurs (None si aucun n'a de partie)
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    avg_kill_participation: float | None = None  # moyenne des moyennes des joueurs
    lp_per_game: float | None = None  # lp_net / parties
    best_win_streak: int = 0
    multikills: int | None = None
    penta_kills: int | None = None
    first_bloods: int | None = None
    dragon_kills: int | None = None
    baron_kills: int | None = None
    turret_kills: int | None = None
    objectives_stolen: int | None = None
    surrenders: int | None = None
    avg_game_duration: int | None = None  # secondes (temps total / parties)
    total_time_played: int | None = None  # secondes
    avg_damage_share: float | None = None
    avg_gold_per_min: float | None = None
    avg_wards_placed: float | None = None
    # Saison : victoires / défaites cumulées des joueurs classés
    season_wins: int | None = None
    season_losses: int | None = None
    season_winrate: float | None = None
    # Parties au-delà du quota quotidien (non comptées) et leurs LP
    games_over_quota: int = 0
    lp_over_quota: int = 0
    # Joker (rempli par `app.api.leaderboard.joker_summary`)
    jokers_total: int = 0
    jokers_used: int = 0
    jokers_left: int = 0
    joker_today: bool = False
    joker_extra_games: int = 0
    can_use_joker: bool = False
    jokers: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _iso_or_none(dt: datetime | None) -> str | None:
    aware = as_utc(dt)
    return aware.isoformat() if aware is not None else None


def _mean_or_none(values: list[float | None], digits: int | None) -> float | None:
    """Moyenne des valeurs non None (arrondie à `digits` décimales, entier si None) ; None si aucune."""
    known = [float(v) for v in values if v is not None]
    if not known:
        return None
    mean = statistics.fmean(known)
    return round(mean) if digits is None else round(mean, digits)


def team_mvp(players: list[PlayerStats]) -> int | None:
    """Joueur « MVP » d'un duo : LP nets, puis winrate (None = plus petit), puis KDA moyen, puis parties.

    Égalité parfaite → le premier de la liste. None sans joueur.
    """
    if not players:
        return None
    best = max(
        players,
        key=lambda p: (
            p.lp_net,
            p.winrate is not None,
            p.winrate or 0.0,
            p.avg_kda is not None,
            p.avg_kda or 0.0,
            p.games,
        ),
    )
    return best.player_id


def compute_team_stats(
    team: Team, players: list[PlayerStats], *, together: tuple[int, int, int] = (0, 0, 0)
) -> TeamStats:
    """Agrège les stats des joueurs d'un duo ; `position` = 0 (rempli par `rank_teams`).

    `together` = (parties, victoires, défaites) jouées ensemble (calculé par
    `app.api.leaderboard.together_record`). Victoires, défaites et parties sont celles du *duo* :
    une partie jouée ensemble (même partie, même côté) compte une fois, pas une par joueur. Les LP
    restent la somme des deux joueurs (chacun gagne ou perd les siens sur la partie).
    """
    together_games, together_wins, together_losses = (int(v) for v in together)
    player_games = sum(p.games for p in players)
    wins = max(0, sum(p.wins for p in players) - together_wins)
    losses = max(0, sum(p.losses for p in players) - together_losses)
    games = max(0, player_games - together_games)
    lp_net = sum(p.lp_net for p in players)
    mvp_id = team_mvp(players)
    mvp = next((p for p in players if p.player_id == mvp_id), None) if mvp_id is not None else None
    ranked = [p for p in players if p.absolute_lp is not None]
    avg_abs = round(statistics.fmean(p.absolute_lp for p in ranked)) if ranked else None  # type: ignore[misc]
    top = max(ranked, key=lambda p: (p.absolute_lp, p.lp_net)) if ranked else None
    total_time = _known_sum([p.total_time_played for p in players])
    season_wins = _known_sum([p.season_wins for p in players])
    season_losses = _known_sum([p.season_losses for p in players])

    def sums(attr: str) -> int | None:
        return _known_sum([getattr(p, attr) for p in players])
    return TeamStats(
        team_id=int(team.id or 0),
        name=team.name,
        color=team.color,
        slot=team.slot,
        position=0,
        window_start=_iso_or_none(team.window_start),
        window_end=_iso_or_none(team.window_end),
        lp_net=lp_net,
        games=games,
        wins=wins,
        losses=losses,
        winrate=winrate(wins, losses),
        games_today=sum(p.games_today for p in players),
        live_count=sum(1 for p in players if p.live is not None),
        players=list(players),
        mvp_player_id=mvp_id,
        mvp_top_champion_splash_url=mvp.top_champion_splash_url if mvp is not None else None,
        # Mêmes arrondis que les stats joueur (KDA et CS/min : 2 décimales, vision : 1, dégâts : entier)
        avg_kda=_mean_or_none([p.avg_kda for p in players], 2),
        avg_cs_per_min=_mean_or_none([p.avg_cs_per_min for p in players], 2),
        avg_vision=_mean_or_none([p.avg_vision for p in players], 1),
        avg_damage=_mean_or_none([p.avg_damage for p in players], None),
        together_games=together_games,
        together_wins=together_wins,
        together_losses=together_losses,
        together_winrate=winrate(together_wins, together_losses),
        avg_absolute_lp=avg_abs,
        rank_label=label_from_absolute_lp(avg_abs),
        rank_color=color_from_absolute_lp(avg_abs),
        top_player_id=top.player_id if top is not None else None,
        top_player_rank_label=top.rank_label if top is not None else None,
        kills=sums("kills"),
        deaths=sums("deaths"),
        assists=sums("assists"),
        avg_kill_participation=_mean_or_none([p.avg_kill_participation for p in players], 1),
        lp_per_game=round(lp_net / games, 1) if games else None,
        best_win_streak=max((p.best_win_streak for p in players), default=0),
        multikills=sums("multikills"),
        penta_kills=sums("penta_kills"),
        first_bloods=sums("first_bloods"),
        dragon_kills=sums("dragon_kills"),
        baron_kills=sums("baron_kills"),
        turret_kills=sums("turret_kills"),
        objectives_stolen=sums("objectives_stolen"),
        surrenders=sums("surrenders"),
        # Temps de jeu cumulé des deux joueurs : la durée moyenne se rapporte donc aux parties jouées
        avg_game_duration=round(total_time / player_games) if total_time is not None and player_games else None,
        total_time_played=total_time,
        avg_damage_share=_mean_or_none([p.avg_damage_share for p in players], 1),
        avg_gold_per_min=_mean_or_none([p.avg_gold_per_min for p in players], 1),
        avg_wards_placed=_mean_or_none([p.avg_wards_placed for p in players], 1),
        season_wins=season_wins,
        season_losses=season_losses,
        season_winrate=winrate(season_wins, season_losses)
        if season_wins is not None and season_losses is not None
        else None,
        games_over_quota=sum(p.games_over_quota for p in players),
        lp_over_quota=sum(p.lp_over_quota for p in players),
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


# ---------------------------------------------------------------------------
# Comparaison des duos, classement des rangs, positions par statistique
# ---------------------------------------------------------------------------

# (clé TeamStats, libellé, unité, plus haut = mieux, format d'affichage)
COMPARISON_METRICS: tuple[tuple[str, str, str, bool, str], ...] = (
    ("lp_net", "LP nets", "LP", True, "lp"),
    ("lp_per_game", "LP par partie", "LP/partie", True, "dec1"),
    ("winrate", "Winrate", "%", True, "pct"),
    ("games", "Parties jouées", "", True, "int"),
    ("together_games", "Parties ensemble", "", True, "int"),
    ("together_winrate", "Winrate ensemble", "%", True, "pct"),
    ("avg_absolute_lp", "Rang moyen", "", True, "rank"),
    ("avg_kda", "KDA moyen", "", True, "dec2"),
    ("avg_kill_participation", "Participation aux kills", "%", True, "pct"),
    ("avg_cs_per_min", "CS/min", "", True, "dec1"),
    ("avg_gold_per_min", "Or/min", "", True, "int"),
    ("avg_damage", "Dégâts aux champions", "", True, "int"),
    ("avg_damage_share", "Part des dégâts", "%", True, "pct"),
    ("avg_vision", "Score de vision", "", True, "dec1"),
    ("avg_wards_placed", "Balises posées", "", True, "dec1"),
    ("best_win_streak", "Meilleure série de victoires", "", True, "int"),
    ("multikills", "Multikills", "", True, "int"),
    ("penta_kills", "Pentakills", "", True, "int"),
    ("first_bloods", "Premiers sangs", "", True, "int"),
    ("dragon_kills", "Dragons", "", True, "int"),
    ("baron_kills", "Barons", "", True, "int"),
    ("turret_kills", "Tourelles", "", True, "int"),
    ("objectives_stolen", "Objectifs volés", "", True, "int"),
    ("avg_game_duration", "Durée moyenne", "s", False, "duration"),
    ("surrenders", "Abandons", "", False, "int"),
    ("season_winrate", "Winrate saison", "%", True, "pct"),
)


def format_metric(value: float | None, fmt: str, unit: str = "") -> str:
    """Valeur affichable en français : "+42 LP", "63 %", "Gold II · 45 LP", "31 min", "—" si None."""
    if value is None:
        return EMPTY_DISPLAY
    suffix = f" {unit}" if unit and unit not in ("%", "s") else ""
    if fmt == "lp":
        return format_signed_lp(value, unit or "LP")
    if fmt == "rank":
        return label_from_absolute_lp(value)
    if fmt == "pct":
        return f"{round(value)} %"
    if fmt == "duration":
        return f"{round(value / 60)} min"
    if unit.startswith("LP"):  # LP par partie : signé
        return format_signed_lp(value, unit, digits=2 if fmt == "dec2" else 1)
    if fmt == "dec1":
        return f"{value:.1f}{suffix}"
    if fmt == "dec2":
        return f"{value:.2f}{suffix}"
    return f"{format_int_fr(value)}{suffix}"


def _best_and_worst(values: list[tuple[int, float]], higher_is_better: bool) -> tuple[int | None, int | None]:
    """(meilleur, pire) duo ; (None, None) si moins de 2 valeurs ou toutes égales. Égalité → le premier."""
    if len(values) < 2 or len({v for _id, v in values}) == 1:
        return None, None
    best = values[0]
    worst = values[0]
    for item in values[1:]:
        if (item[1] > best[1]) if higher_is_better else (item[1] < best[1]):
            best = item
        if (item[1] < worst[1]) if higher_is_better else (item[1] > worst[1]):
            worst = item
    return best[0], worst[0]


def compare_teams(teams: list[TeamStats]) -> dict[str, Any]:
    """Tableau comparatif des duos (ordre de `COMPARISON_METRICS`, duos dans l'ordre donné)."""
    metrics = []
    for key, label, unit, higher_is_better, fmt in COMPARISON_METRICS:
        values = []
        known: list[tuple[int, float]] = []
        for team in teams:
            value = getattr(team, key)
            values.append({"team_id": team.team_id, "value": value, "display": format_metric(value, fmt, unit)})
            if value is not None:
                known.append((team.team_id, value))
        best, worst = _best_and_worst(known, higher_is_better)
        metrics.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "higher_is_better": higher_is_better,
                "format": fmt,
                "values": values,
                "best_team_id": best,
                "worst_team_id": worst,
            }
        )
    return {"metrics": metrics}


def _name_key(p: PlayerStats) -> str:
    return p.display_name.casefold()


def build_rank_ladder(teams: list[TeamStats], unassigned: list[PlayerStats]) -> dict[str, Any]:
    """Classement net des rangs : joueurs (absolu desc, LP nets desc, pseudo ; non classés à la fin,
    position None), duos (rang moyen desc), répartition par tier et résumé.

    `teams` portent leurs joueurs ; `unassigned` = joueurs actifs hors duo (doublons ignorés).
    """
    team_of: dict[int, TeamStats] = {}
    everyone: list[PlayerStats] = []
    for team in teams:
        for p in team.players:
            if p.player_id not in team_of:
                team_of[p.player_id] = team
                everyone.append(p)
    seen = set(team_of)
    for p in unassigned:
        if p.player_id not in seen:
            seen.add(p.player_id)
            everyone.append(p)

    ranked = sorted(
        (p for p in everyone if p.absolute_lp is not None),
        key=lambda p: (-(p.absolute_lp or 0), -p.lp_net, _name_key(p)),
    )
    unranked = sorted((p for p in everyone if p.absolute_lp is None), key=_name_key)
    baseline_order = sorted(
        (p for p in everyone if p.baseline_absolute_lp is not None),
        key=lambda p: (-(p.baseline_absolute_lp or 0), _name_key(p)),
    )
    baseline_positions = {p.player_id: index for index, p in enumerate(baseline_order, start=1)}

    players = []
    for index, p in enumerate(ranked + unranked, start=1):
        position = index if p.absolute_lp is not None else None
        baseline_position = baseline_positions.get(p.player_id)
        team = team_of.get(p.player_id)
        players.append(
            {
                "position": position,
                "player_id": p.player_id,
                "display_name": p.display_name,
                "icon_url": p.icon_url,
                "summoner_level": p.summoner_level,
                "team_id": team.team_id if team is not None else p.team_id,
                "team_name": team.name if team is not None else None,
                "team_color": team.color if team is not None else None,
                "tier": p.tier if p.absolute_lp is not None else None,
                "rank": p.rank if p.absolute_lp is not None else None,
                "lp": p.lp,
                "rank_label": p.rank_label if p.absolute_lp is not None else UNRANKED_LABEL_FR,
                "rank_color": p.rank_color,
                "rank_emblem_url": p.rank_emblem_url,
                "rank_crest_url": p.rank_crest_url,
                "absolute_lp": p.absolute_lp,
                "baseline_absolute_lp": p.baseline_absolute_lp,
                "baseline_rank_label": label_from_absolute_lp(p.baseline_absolute_lp),
                "baseline_position": baseline_position,
                "position_delta": baseline_position - position
                if baseline_position is not None and position is not None
                else None,
                "lp_net": p.lp_net,
                "rank_delta_lp": p.rank_delta_lp,
                "season_wins": p.season_wins,
                "season_losses": p.season_losses,
                "season_winrate": p.season_winrate,
                "hot_streak": p.hot_streak,
                "peak_rank_label": p.peak_rank_label,
                "promotions": p.promotions,
                "demotions": p.demotions,
                "is_linked": p.is_linked,
                "live": p.live,
                "games": p.games,
                "winrate": p.winrate,
            }
        )

    ordered_teams = sorted(
        teams,
        key=lambda t: (t.avg_absolute_lp is None, -(t.avg_absolute_lp or 0), -t.lp_net, t.name.casefold()),
    )
    team_rows = []
    for index, team in enumerate(ordered_teams, start=1):
        team_rows.append(
            {
                "position": index if team.avg_absolute_lp is not None else None,
                "team_id": team.team_id,
                "name": team.name,
                "color": team.color,
                "avg_absolute_lp": team.avg_absolute_lp,
                "rank_label": team.rank_label,
                "rank_color": team.rank_color,
                "top_player_id": team.top_player_id,
                "top_player_rank_label": team.top_player_rank_label,
                "players": [p.player_id for p in team.players],
                "lp_net": team.lp_net,
            }
        )

    tiers = []
    for tier in [*reversed(TIERS), "UNRANKED"]:
        members = (
            [p for p in ranked if _norm_tier(p.tier) == tier] if tier != "UNRANKED" else unranked
        )
        if members:
            tiers.append(
                {
                    "tier": tier,
                    "label": TIER_LABELS_FR[tier],
                    "color": RANK_COLORS[tier],
                    "count": len(members),
                    "players": [p.display_name for p in members],
                }
            )

    def brief(p: PlayerStats) -> dict[str, Any]:
        return {"player_id": p.player_id, "display_name": p.display_name, "rank_label": p.rank_label}

    avg_abs = round(statistics.fmean(p.absolute_lp for p in ranked)) if ranked else None  # type: ignore[misc]
    return {
        "players": players,
        "teams": team_rows,
        "tiers": tiers,
        "summary": {
            "ranked_players": len(ranked),
            "unranked_players": len(unranked),
            "highest": brief(ranked[0]) if ranked else None,
            "lowest": brief(ranked[-1]) if ranked else None,
            "avg_absolute_lp": avg_abs,
            "avg_rank_label": label_from_absolute_lp(avg_abs),
        },
    }


# (clé PlayerStats, plus haut = mieux) : positions de la fiche joueur
RANKING_METRICS: tuple[tuple[str, bool], ...] = (
    ("absolute_lp", True),
    ("lp_net", True),
    ("lp_per_game", True),
    ("winrate", True),
    ("games", True),
    ("avg_kda", True),
    ("avg_kills", True),
    ("avg_deaths", False),
    ("avg_assists", True),
    ("avg_kill_participation", True),
    ("avg_cs_per_min", True),
    ("avg_gold_per_min", True),
    ("avg_damage", True),
    ("avg_damage_share", True),
    ("avg_vision", True),
    ("avg_wards_placed", True),
    ("best_win_streak", True),
    ("multikills", True),
    ("penta_kills", True),
    ("first_bloods", True),
    ("dragon_kills", True),
    ("turret_kills", True),
)


def metric_rankings(players: list[PlayerStats], player_id: int) -> dict[str, dict[str, Any]]:
    """Position du joueur sur chaque statistique de `RANKING_METRICS`, parmi les joueurs actifs liés.

    Classement « compétition » (1, 1, 3) ; les valeurs None sont exclues du total et donnent une
    position None. Un joueur hors de ce groupe (inactif, non lié) garde sa valeur, sans position.
    """
    pool = [p for p in players if p.active and p.is_linked]
    target = next((p for p in players if p.player_id == player_id), None)
    in_pool = any(p.player_id == player_id for p in pool)
    result: dict[str, dict[str, Any]] = {}
    for key, higher_is_better in RANKING_METRICS:
        values = [getattr(p, key) for p in pool if getattr(p, key) is not None]
        value = getattr(target, key) if target is not None else None
        position = None
        if in_pool and value is not None:
            position = 1 + sum(1 for v in values if (v > value if higher_is_better else v < value))
        result[key] = {"position": position, "total": len(values), "value": value, "higher_is_better": higher_is_better}
    return result
