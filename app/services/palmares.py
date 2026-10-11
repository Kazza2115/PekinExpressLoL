"""Palmarès d'une édition : vainqueurs, trophées individuels, victoires et exploits de chacun.

Tout est calculé à partir des stats du challenge (`app.api.leaderboard.build_leaderboard`) :

- le podium des duos (LP nets) et quelques trophées de duo ;
- les trophées individuels : chaque trophée va au(x) meilleur(s) joueur(s) sur une statistique
  (ex aequo partagés). Les moyennes demandent un minimum de parties ; un trophée de compte
  (pentakills, objectifs volés…) n'est pas décerné si personne n'en a, ni quand tous les joueurs
  sont à égalité. Les « trophées de la honte » sont pour rire ;
- pour chaque joueur : son bilan (victoires, défaites, rang de départ et d'arrivée), ses
  trophées, ses exploits (pentakills, records, séries…) et ses plus belles victoires.

Le palmarès d'une édition terminée est figé dans la table `Edition` au moment de la
réinitialisation du challenge : la page garde ainsi les éditions précédentes.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import Session, col, select

from app.db.models import Challenge, ChallengeStatus, Edition, MatchParticipant, Queue, Team, game_end_of, utcnow
from app.db.session import as_utc
from app.riot import ddragon
from app.services.double_lp import counted_lp
from app.services.stats import (
    PlayerStats,
    TeamStats,
    format_duration,
    format_int_fr,
    format_signed_lp,
    kda,
    label_from_absolute_lp,
)

# Moyennes (KDA, CS/min…) : au moins 3 parties ; winrate : au moins 5
MIN_GAMES_AVERAGE = 3
MIN_GAMES_WINRATE = 5
MIN_TOGETHER_GAMES = 3
EXPLOITS_MAX = 9
BEST_WINS_MAX = 3
STARTED_STATUSES = (ChallengeStatus.RUNNING, ChallengeStatus.FINISHED)


def edition_label(number: int) -> str:
    """1 → « 1re édition », 2 → « 2e édition »."""
    return f"{number}{'re' if number == 1 else 'e'} édition"


def _fr(value: float, digits: int = 1) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def _plural(count: float, singular: str, plural: str | None = None) -> str:
    return f"{format_int_fr(count)} {singular if abs(count) < 2 else (plural or singular + 's')}"


def _hours(seconds: float) -> str:
    total = round(seconds / 60)
    hours, minutes = divmod(total, 60)
    return f"{hours} h {minutes:02d}" if hours else f"{minutes} min"


def _sum(*values: int | None) -> int | None:
    known = [int(v) for v in values if v is not None]
    return sum(known) if known else None


def _record(p: PlayerStats, key: str) -> dict | None:
    record = (p.records or {}).get(key)
    return record if isinstance(record, dict) else None


def _multikills_label(p: PlayerStats, value: float) -> str:
    parts = [
        _plural(n, label)
        for n, label in ((p.penta_kills, "penta"), (p.quadra_kills, "quadra"), (p.triple_kills, "triple"), (p.double_kills, "double"))
        if n
    ]
    return f"{_plural(value, 'multikill')} ({' · '.join(parts)})" if parts else _plural(value, "multikill")


@dataclass(frozen=True)
class TrophySpec:
    key: str
    icon: str
    title: str
    description: str
    value: Callable[[PlayerStats], float | None]
    label: Callable[[PlayerStats, float], str]
    category: str = "gloire"  # « gloire » ou « honte » (pour rire)
    higher: bool = True
    min_games: int = 1
    min_value: float | None = None  # pas décerné en dessous (ex. aucun pentakill)
    min_players: int = 1  # ex. « Lanterne rouge » : au moins 2 joueurs


TROPHIES: tuple[TrophySpec, ...] = (
    TrophySpec("mvp", "👑", "MVP du challenge", "Le plus de LP nets", lambda p: p.lp_net,
               lambda p, v: f"{format_signed_lp(v)} nets"),
    TrophySpec("wins", "🏅", "Machine à gagner", "Le plus de victoires", lambda p: p.wins,
               lambda p, v: f"{_plural(v, 'victoire')} sur {_plural(p.games, 'partie')}", min_value=1),
    TrophySpec("winrate", "🎯", "Sniper", f"Meilleur winrate ({MIN_GAMES_WINRATE} parties min.)", lambda p: p.winrate,
               lambda p, v: f"{round(v)} % ({p.wins} V / {p.losses} D)", min_games=MIN_GAMES_WINRATE),
    TrophySpec("streak", "🔥", "Inarrêtable", "Plus longue série de victoires", lambda p: p.best_win_streak,
               lambda p, v: f"{_plural(v, 'victoire')} d'affilée", min_value=2),
    TrophySpec("climber", "🚀", "Ascenseur", "Le plus de divisions gagnées", lambda p: p.promotions,
               lambda p, v: f"{_plural(v, 'promotion')} · pic {p.peak_rank_label or '—'}", min_value=1),
    TrophySpec("peak", "⛰️", "Tout là-haut", "Plus haut rang atteint pendant le challenge", lambda p: p.peak_absolute_lp,
               lambda p, v: p.peak_rank_label or label_from_absolute_lp(v)),
    TrophySpec("jackpot", "🎰", "Jackpot", "Plus gros gain de LP en une partie", lambda p: p.best_lp_gain,
               lambda p, v: format_signed_lp(v), min_value=1),
    TrophySpec("kda", "⚔️", "Intouchable", f"Meilleur KDA moyen ({MIN_GAMES_AVERAGE} parties min.)", lambda p: p.avg_kda,
               lambda p, v: f"KDA {_fr(v, 2)}", min_games=MIN_GAMES_AVERAGE),
    TrophySpec("kills", "💀", "Le boucher", "Le plus de kills", lambda p: p.kills,
               lambda p, v: _plural(v, "kill"), min_value=1),
    TrophySpec("carnage", "🔪", "Carnage", "Le plus de kills en une seule partie",
               lambda p: (_record(p, "most_kills") or {}).get("value"),
               lambda p, v: f"{(_record(p, 'most_kills') or {}).get('label') or _plural(v, 'kill')}"
                            f" avec {(_record(p, 'most_kills') or {}).get('champion_name') or '?'}", min_value=1),
    TrophySpec("assists", "🤝", "Le plus généreux", "Le plus d'assists", lambda p: p.assists,
               lambda p, v: _plural(v, "assist"), min_value=1),
    TrophySpec("multikills", "🌟", "Roi des multikills", "Le plus de multikills (doubles, triples, quadras, pentas)",
               lambda p: p.multikills, _multikills_label, min_value=1),
    TrophySpec("first_blood", "🩸", "Premier sang", "Le plus de premiers sangs", lambda p: p.first_bloods,
               lambda p, v: _plural(v, "premier sang", "premiers sangs"), min_value=1),
    TrophySpec("damage", "💥", "Canon à dégâts", f"Le plus de dégâts par minute ({MIN_GAMES_AVERAGE} parties min.)",
               lambda p: p.avg_damage_per_min, lambda p, v: f"{format_int_fr(v)} dégâts/min", min_games=MIN_GAMES_AVERAGE),
    TrophySpec("farm", "🌾", "Fermier en chef", f"Le plus de CS par minute ({MIN_GAMES_AVERAGE} parties min.)",
               lambda p: p.avg_cs_per_min, lambda p, v: f"{_fr(v)} CS/min", min_games=MIN_GAMES_AVERAGE),
    TrophySpec("gold", "💰", "Picsou", f"Le plus d'or par minute ({MIN_GAMES_AVERAGE} parties min.)",
               lambda p: p.avg_gold_per_min, lambda p, v: f"{format_int_fr(v)} or/min", min_games=MIN_GAMES_AVERAGE),
    TrophySpec("vision", "👁️", "Œil de Sauron", f"Meilleur score de vision moyen ({MIN_GAMES_AVERAGE} parties min.)",
               lambda p: p.avg_vision, lambda p, v: f"{_fr(v)} de vision par partie", min_games=MIN_GAMES_AVERAGE),
    TrophySpec("monsters", "🐉", "Chasseur de monstres", "Le plus de dragons et de barons",
               lambda p: _sum(p.dragon_kills, p.baron_kills),
               lambda p, v: f"{_plural(p.dragon_kills or 0, 'dragon')} · {_plural(p.baron_kills or 0, 'baron')}", min_value=1),
    TrophySpec("towers", "🏰", "Démolisseur", "Le plus de tourelles détruites", lambda p: p.turret_kills,
               lambda p, v: _plural(v, "tourelle"), min_value=1),
    TrophySpec("steals", "🥷", "Voleur", "Le plus d'objectifs volés", lambda p: p.objectives_stolen,
               lambda p, v: _plural(v, "objectif volé", "objectifs volés"), min_value=1),
    TrophySpec("tank", "🛡️", "Mur de briques", f"Le plus de dégâts encaissés par partie ({MIN_GAMES_AVERAGE} parties min.)",
               lambda p: p.avg_damage_taken, lambda p, v: f"{format_int_fr(v)} par partie", min_games=MIN_GAMES_AVERAGE),
    TrophySpec("healer", "💚", "Infirmier", f"Le plus de soins par partie ({MIN_GAMES_AVERAGE} parties min.)",
               lambda p: p.avg_heal, lambda p, v: f"{format_int_fr(v)} PV soignés par partie",
               min_games=MIN_GAMES_AVERAGE, min_value=1),
    TrophySpec("cc", "🧊", "Maître du contrôle", f"Le plus de contrôle infligé par partie ({MIN_GAMES_AVERAGE} parties min.)",
               lambda p: p.avg_cc_time, lambda p, v: f"{format_duration(v)} de CC par partie",
               min_games=MIN_GAMES_AVERAGE, min_value=1),
    TrophySpec("grinder", "🎮", "No-life", "Le plus de parties jouées", lambda p: p.games,
               lambda p, v: _plural(v, "partie"), min_value=1),
    TrophySpec("marathon", "⏱️", "Marathonien", "Le plus de temps de jeu", lambda p: p.total_time_played,
               lambda p, v: _hours(v), min_value=1),
    TrophySpec("one_trick", "🎭", "One-trick", "Le plus de parties sur un même champion", lambda p: p.top_champion_games,
               lambda p, v: f"{_plural(v, 'partie')} sur {p.top_champion or '?'}", min_value=2),
    TrophySpec("versatile", "🃏", "Couteau suisse", "Le plus de champions différents", lambda p: len(p.champions or []),
               lambda p, v: _plural(v, "champion"), min_value=2),
    # --- Trophées de la honte (pour rire) ---
    TrophySpec("red_lantern", "🪦", "Lanterne rouge", "Le moins de LP nets", lambda p: p.lp_net,
               lambda p, v: f"{format_signed_lp(v)} nets", category="honte", higher=False, min_games=0, min_players=2),
    TrophySpec("deaths", "☠️", "Abonné à l'écran gris", "Le plus de morts", lambda p: p.deaths,
               lambda p, v: _plural(v, "mort"), category="honte", min_value=1),
    TrophySpec("dead_time", "👻", "Fantôme", f"Le plus de temps passé mort par partie ({MIN_GAMES_AVERAGE} parties min.)",
               lambda p: p.avg_time_dead, lambda p, v: f"{format_duration(v)} mort par partie", category="honte",
               min_games=MIN_GAMES_AVERAGE, min_value=1),
    TrophySpec("loss_streak", "🥶", "Série noire", "Plus longue série de défaites", lambda p: p.best_loss_streak,
               lambda p, v: f"{_plural(v, 'défaite')} d'affilée", category="honte", min_value=2),
    TrophySpec("ff", "🏳️", "Roi du /ff", "Le plus de défaites par abandon", lambda p: p.surrenders,
               lambda p, v: _plural(v, "abandon"), category="honte", min_value=1),
    TrophySpec("aegis", "⚡", "Abonné à l'Aegis", "Le plus de double LP (bonus retiré)", lambda p: p.double_lp_games,
               lambda p, v: f"{_plural(v, 'double LP', 'double LP')} · {p.lp_double_bonus} LP retirés", category="honte",
               min_value=1),
    TrophySpec("outlaw", "⛔", "Hors-la-loi", "Le plus de parties hors quota", lambda p: p.games_over_quota,
               lambda p, v: _plural(v, "partie hors quota", "parties hors quota"), category="honte", min_value=1),
    TrophySpec("tourist", "🧳", "Touriste", "Le moins de parties jouées", lambda p: p.games,
               lambda p, v: _plural(v, "partie"), category="honte", higher=False, min_games=0, min_players=2),
)


def _player_ref(p: PlayerStats, teams_by_id: dict[int, TeamStats]) -> dict[str, Any]:
    team = teams_by_id.get(p.team_id) if p.team_id is not None else None
    return {
        "player_id": p.player_id,
        "display_name": p.display_name,
        "icon_url": p.icon_url,
        "team_id": p.team_id,
        "team_name": team.name if team is not None else None,
        "team_color": team.color if team is not None else None,
    }


def award(spec: TrophySpec, players: list[PlayerStats]) -> list[tuple[PlayerStats, float]]:
    """Gagnant(s) d'un trophée (ex aequo partagés) ; vide si personne n'est éligible."""
    if len(players) < spec.min_players:
        return []
    pool: list[tuple[PlayerStats, float]] = []  # joueurs éligibles (assez de parties, valeur connue)
    for p in players:
        if p.games < spec.min_games:
            continue
        value = spec.value(p)
        if value is not None:
            pool.append((p, float(value)))
    candidates = [(p, v) for p, v in pool if spec.min_value is None or v >= spec.min_value]
    if not candidates:
        return []
    best = (max if spec.higher else min)(value for _p, value in candidates)
    winners = [(p, value) for p, value in candidates if abs(value - best) < 1e-9]
    if len(winners) == len(pool) >= 3:
        return []  # tous les joueurs éligibles à égalité (ex. 10 parties chacun) : personne ne se distingue
    return winners


def award_trophies(players: list[PlayerStats], teams_by_id: dict[int, TeamStats]) -> list[dict[str, Any]]:
    """Tous les trophées individuels, dans l'ordre de `TROPHIES` (gagnants vides : non décerné)."""
    trophies = []
    for spec in TROPHIES:
        winners = award(spec, players)
        trophies.append(
            {
                "key": spec.key,
                "icon": spec.icon,
                "title": spec.title,
                "description": spec.description,
                "category": spec.category,
                "winners": [
                    {**_player_ref(p, teams_by_id), "value": value, "label": spec.label(p, value)}
                    for p, value in sorted(winners, key=lambda w: w[0].display_name.lower())
                ],
            }
        )
    return trophies


def team_trophies(teams: list[TeamStats]) -> list[dict[str, Any]]:
    """Trophées de duo (en plus du podium)."""

    def entry(key: str, icon: str, title: str, description: str, picked: list[TeamStats], label: Callable[[TeamStats], str]):
        return {
            "key": key,
            "icon": icon,
            "title": title,
            "description": description,
            "winners": [
                {"team_id": t.team_id, "name": t.name, "color": t.color, "label": label(t),
                 "players": [p.display_name for p in t.players]}
                for t in picked
            ],
        }

    playing = [t for t in teams if t.games > 0]

    def best(values: list[tuple[TeamStats, float]]) -> list[TeamStats]:
        if not values:
            return []
        top = max(v for _t, v in values)
        picked = [t for t, v in values if abs(v - top) < 1e-9]
        # Tous les duos à égalité (au moins 3) : rien ne les distingue
        return [] if len(picked) == len(playing) >= 3 else picked

    together = [(t, t.together_winrate) for t in teams if t.together_games >= MIN_TOGETHER_GAMES and t.together_winrate is not None]
    games = [(t, float(t.games)) for t in teams if t.games > 0]
    streak = [(t, float(t.best_win_streak)) for t in teams if t.best_win_streak >= 2]
    return [
        entry("together", "💞", "Duo fusionnel", f"Meilleur winrate en jouant ensemble ({MIN_TOGETHER_GAMES} parties min.)",
              best(together), lambda t: f"{round(t.together_winrate or 0)} % sur {_plural(t.together_games, 'partie')} ensemble"),
        entry("grind", "🏃", "Duo increvable", "Le plus de parties jouées", best(games),
              lambda t: _plural(t.games, "partie")),
        entry("streak", "🔥", "Duo en feu", "Plus longue série de victoires d'un des deux joueurs", best(streak),
              lambda t: f"{_plural(t.best_win_streak, 'victoire')} d'affilée"),
    ]


def _exploits(p: PlayerStats) -> list[dict[str, str]]:
    """Exploits individuels marquants (au plus `EXPLOITS_MAX`), du plus rare au plus courant."""
    out: list[dict[str, str]] = []

    def add(icon: str, text: str) -> None:
        out.append({"icon": icon, "text": text})

    if p.penta_kills:
        add("🌟", f"{_plural(p.penta_kills, 'PENTAKILL', 'PENTAKILLS')} !")
    if p.quadra_kills:
        add("✨", _plural(p.quadra_kills, "quadrakill"))
    if p.objectives_stolen:
        add("🥷", _plural(p.objectives_stolen, "objectif volé", "objectifs volés"))
    if p.best_win_streak >= 3:
        add("🔥", f"Série de {p.best_win_streak} victoires d'affilée")
    if p.promotions:
        add("🚀", f"{_plural(p.promotions, 'promotion')}, pic à {p.peak_rank_label or '—'}")
    for key, icon, prefix in (
        ("best_kda", "⚔️", "Meilleure partie"),
        ("most_kills", "💀", "Record de kills"),
        ("most_damage", "💥", "Record de dégâts"),
        ("biggest_lp_gain", "🎰", "Plus gros gain"),
    ):
        record = _record(p, key)
        if record is not None and record.get("label"):
            champion = record.get("champion_name")
            add(icon, f"{prefix} : {record['label']}" + (f" avec {champion}" if champion and key != "biggest_lp_gain" else ""))
    if (p.largest_killing_spree or 0) >= 8:
        add("🩸", f"{p.largest_killing_spree} kills sans mourir")
    if p.first_bloods:
        add("🩸", _plural(p.first_bloods, "premier sang", "premiers sangs"))
    if p.surrender_wins:
        add("🏳️", _plural(p.surrender_wins, "victoire par abandon adverse", "victoires par abandon adverse"))
    if p.top_champion and p.top_champion_games >= 2:
        rate = f", {round(p.top_champion_winrate)} % de victoires" if p.top_champion_winrate is not None else ""
        add("🎭", f"Champion fétiche : {p.top_champion} ({_plural(p.top_champion_games, 'partie')}{rate})")
    return out[:EXPLOITS_MAX]


def _best_wins(participants: list[MatchParticipant], stats: PlayerStats, window: tuple[datetime | None, datetime | None]) -> list[dict]:
    """Plus belles victoires comptées (KDA, puis kills) : champion, score, LP."""
    start, end = (as_utc(window[0]), as_utc(window[1]))
    excluded = set(stats.over_quota_match_ids)
    wins = [
        p for p in participants
        if p.win and not p.is_remake and p.queue in (Queue.SOLO, Queue.SOLO.value) and p.match_id not in excluded
        and (start is None or game_end_of(p) >= start) and (end is None or game_end_of(p) <= end)
    ]
    wins.sort(key=lambda p: (kda(p.kills, p.deaths, p.assists), p.kills, -p.deaths), reverse=True)
    return [
        {
            "match_id": p.match_id,
            "champion_name": p.champion_name,
            "champion_icon_url": ddragon.champion_icon_url(ddragon.CURRENT_VERSION, p.champion_name),
            "kills": p.kills,
            "deaths": p.deaths,
            "assists": p.assists,
            "kda": round(kda(p.kills, p.deaths, p.assists), 2),
            "lp": counted_lp(p),
            "game_end": game_end_of(p).isoformat(),
        }
        for p in wins[:BEST_WINS_MAX]
    ]


def _team_summary(t: TeamStats) -> dict[str, Any]:
    return {
        "position": t.position,
        "team_id": t.team_id,
        "name": t.name,
        "color": t.color,
        "lp_net": t.lp_net,
        "games": t.games,
        "wins": t.wins,
        "losses": t.losses,
        "winrate": t.winrate,
        "rank_label": t.rank_label,
        "players": [
            {"player_id": p.player_id, "display_name": p.display_name, "icon_url": p.icon_url, "lp_net": p.lp_net,
             "rank_label": p.rank_label}
            for p in t.players
        ],
    }


def next_edition_number(session: Session) -> int:
    """Numéro de l'édition en cours : éditions archivées + 1."""
    last = session.exec(select(Edition.number).order_by(col(Edition.number).desc())).first()
    return int(last or 0) + 1


def build_palmares(session: Session, challenge: Challenge, *, now: datetime | None = None) -> dict[str, Any]:
    """Palmarès de l'édition en cours (provisoire tant que le challenge n'est pas terminé)."""
    # Import local : app.api.leaderboard dépend des services (pas l'inverse au chargement)
    from app.api.leaderboard import build_leaderboard, load_history, team_window

    now = now or utcnow()
    teams, players = build_leaderboard(session, challenge, now=now)
    teams_by_id = {t.team_id: t for t in teams}
    team_rows = {t.id: t for t in session.exec(select(Team)).all() if t.id is not None}
    _snapshots, participants_by = load_history(session, [p.player_id for p in players])
    ordered = sorted(players, key=lambda p: (-p.lp_net, -(p.winrate or 0), p.display_name.lower()))
    trophies = award_trophies(ordered, teams_by_id)
    won: dict[int, list[dict[str, str]]] = {}
    for trophy in trophies:
        for winner in trophy["winners"]:
            won.setdefault(winner["player_id"], []).append(
                {"key": trophy["key"], "icon": trophy["icon"], "title": trophy["title"], "category": trophy["category"]}
            )
    number = next_edition_number(session)
    player_rows = []
    for position, p in enumerate(ordered, start=1):
        window = team_window(challenge, team_rows.get(p.team_id) if p.team_id is not None else None)
        player_rows.append(
            {
                **_player_ref(p, teams_by_id),
                "position": position,
                "rank_label": p.rank_label,
                "rank_color": p.rank_color,
                "start_rank_label": label_from_absolute_lp(p.baseline_absolute_lp)
                if p.baseline_absolute_lp is not None else None,
                "peak_rank_label": p.peak_rank_label,
                "lp_net": p.lp_net,
                "games": p.games,
                "wins": p.wins,
                "losses": p.losses,
                "winrate": p.winrate,
                "avg_kda": p.avg_kda,
                "best_win_streak": p.best_win_streak,
                "trophies": won.get(p.player_id, []),
                "exploits": _exploits(p),
                "best_wins": _best_wins(participants_by.get(p.player_id, []), p, window),
            }
        )
    finished = challenge.status == ChallengeStatus.FINISHED
    return {
        "edition": number,
        "edition_label": edition_label(number),
        "name": challenge.name,
        "status": challenge.status.value if isinstance(challenge.status, ChallengeStatus) else challenge.status,
        "final": finished,
        "start_at": _iso(challenge.start_at),
        "end_at": _iso(challenge.end_at),
        "generated_at": now.isoformat(),
        "podium": [_team_summary(t) for t in teams[:3] if t.players],
        "teams": [_team_summary(t) for t in teams],
        "team_trophies": team_trophies(teams),
        "trophies": trophies,
        "players": player_rows,
    }


def _iso(value: datetime | None) -> str | None:
    aware = as_utc(value)
    return aware.isoformat() if aware is not None else None


def archive_edition(session: Session, challenge: Challenge) -> Edition | None:
    """Fige le palmarès de l'édition terminée (avant une réinitialisation). None si rien à garder."""
    if challenge.status != ChallengeStatus.FINISHED:
        return None
    payload = build_palmares(session, challenge)
    if not payload["players"]:
        return None
    edition = Edition(
        number=int(payload["edition"]),
        name=challenge.name,
        start_at=challenge.start_at,
        end_at=challenge.end_at,
        payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    session.add(edition)
    session.flush()
    return edition


def archived_editions(session: Session) -> list[dict[str, Any]]:
    """Palmarès des éditions précédentes, de la plus récente à la plus ancienne."""
    editions = session.exec(select(Edition).order_by(col(Edition.number).desc(), col(Edition.id).desc())).all()
    out = []
    for edition in editions:
        try:
            payload = json.loads(edition.payload)
        except ValueError:
            continue
        payload["archived"] = True
        out.append(payload)
    return out


def palmares_overview(session: Session, challenge: Challenge) -> dict[str, Any]:
    """Réponse de `/api/palmares` : édition en cours (si démarrée) et éditions archivées."""
    current = build_palmares(session, challenge) if challenge.status in STARTED_STATUSES else None
    return {
        "current": current,
        "editions": archived_editions(session),
        "announced_at": _iso(challenge.palmares_announced_at),
    }
