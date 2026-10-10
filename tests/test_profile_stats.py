"""Profil joueur : stats détaillées, répartitions, records, comparatif des duos, classement des rangs."""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.db.models import MatchParticipant, Player, Queue, RankSnapshot, Team, game_end_of
from app.services.stats import (
    COMPARISON_METRICS,
    RANK_COLORS,
    RANKING_METRICS,
    RECORD_KEYS,
    PlayerStats,
    TeamStats,
    build_rank_ladder,
    color_from_absolute_lp,
    compare_teams,
    compute_player_stats,
    compute_team_stats,
    format_duration,
    format_metric,
    format_signed_lp,
    fr_day_label,
    label_from_absolute_lp,
    metric_rankings,
    partner_record,
    rank_progress,
)
from tests.test_stats import make_stats, make_team_stats

PARIS = ZoneInfo("Europe/Paris")
WINDOW_START = datetime(2026, 10, 10, 0, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def snap(captured_at: datetime, tier: str | None, rank: str | None, lp: int, **overrides) -> RankSnapshot:
    data = dict(player_id=1, queue=Queue.SOLO, tier=tier, rank=rank, lp=lp, captured_at=captured_at)
    data.update(overrides)
    return RankSnapshot(**data)


_counter = 0


def game(start: datetime, duration: int, *, win: bool, **fields) -> MatchParticipant:
    global _counter
    _counter += 1
    data = dict(
        match_id=f"EUW1_P{_counter}", player_id=1, queue=Queue.SOLO, game_start=start, game_duration=duration,
        is_remake=False, champion_name="Ahri", win=win, kills=5, deaths=5, assists=5, cs=180, gold=10000,
        damage_to_champions=15000, vision_score=20,
    )
    data.update(fields)
    return MatchParticipant(**data)


def scenario() -> tuple[Player, list[RankSnapshot], list[MatchParticipant]]:
    player = Player(id=1, display_name="Mike", game_name="Mike", tag_line="EUW", puuid="p-mike",
                    linked_at=utc(2026, 10, 1), summoner_level=321, profile_icon_id=7)
    snapshots = [
        snap(utc(2026, 10, 8, 12, 0), "SILVER", "I", 50),  # avant la référence : ignoré
        snap(utc(2026, 10, 9, 20, 0), "GOLD", "I", 90, wins=50, losses=40),  # référence
        snap(utc(2026, 10, 10, 7, 0), "PLATINUM", "IV", 15),  # promotion
        snap(utc(2026, 10, 10, 13, 0), "GOLD", "I", 95),  # rétrogradation
        snap(utc(2026, 10, 10, 21, 30), "PLATINUM", "IV", 10),  # promotion
        snap(utc(2026, 10, 11, 2, 0), "PLATINUM", "IV", 28, wins=53, losses=41),  # dernier
        snap(utc(2026, 10, 11, 3, 0), "DIAMOND", "I", 0, queue=Queue.FLEX),  # autre file
    ]
    participants = [
        game(utc(2026, 10, 9, 10, 0), 1800, win=True, kills=30),  # avant la fenêtre
        # 08:00 à Paris (matin), 20 min, côté bleu
        game(utc(2026, 10, 10, 6, 0), 1200, win=True, kills=10, deaths=2, assists=8, cs=200, gold=12000,
             damage_to_champions=24000, vision_score=30, position="MIDDLE", team_side=100, lp_change=25,
             kill_participation=60.0, damage_share=30.0, double_kills=2, triple_kills=1, penta_kills=0,
             largest_multi_kill=3, largest_killing_spree=6, first_blood_kill=True, turret_kills=2, dragon_kills=0,
             baron_kills=0, objectives_stolen=0, damage_taken=15000, total_heal=3000, time_ccing_others=20,
             time_spent_dead=30, wards_placed=10, wards_killed=2, control_wards_bought=2, surrendered=False,
             champion_id=103),
        # 14:00 à Paris (après-midi), 30 min, côté rouge ; détails inconnus (NULL → 0)
        game(utc(2026, 10, 10, 12, 0), 1800, win=False, kills=2, deaths=8, assists=4, cs=150, gold=9000,
             damage_to_champions=12000, vision_score=20, position="MIDDLE", team_side=200, lp_change=-20,
             damage_share=15.0, surrendered=True, wards_placed=6),
        game(utc(2026, 10, 10, 14, 0), 180, win=False, is_remake=True, kills=40),  # remake : ignoré
        # 22:30 à Paris (soirée), 40 min, KDA parfait
        game(utc(2026, 10, 10, 20, 30), 2400, win=True, kills=5, deaths=0, assists=15, cs=300, gold=15000,
             damage_to_champions=30000, vision_score=50, position="UTILITY", team_side=100,
             kill_participation=80.0, penta_kills=1, largest_multi_kill=5, dragon_kills=1, baron_kills=1,
             objectives_stolen=1, champion_name="Lux", champion_id=99),
        # 03:00 à Paris le 11 (nuit), exactement 25 min, poste et côté inconnus
        game(utc(2026, 10, 11, 1, 0), 1500, win=True, kills=4, deaths=4, assists=4, cs=160, gold=10000,
             damage_to_champions=16000, vision_score=15, lp_change=18, champion_name="Zed"),
        game(utc(2026, 10, 11, 2, 0), 1500, win=True, kills=50, queue=Queue.FLEX),  # autre file
    ]
    return player, snapshots, participants


def compute(**overrides) -> PlayerStats:
    player, snapshots, participants = scenario()
    kwargs = dict(player=player, snapshots=snapshots, participants=participants, window_start=WINDOW_START,
                  window_end=None, games_limit=10, tz=PARIS, now=NOW)
    kwargs.update(overrides)
    return compute_player_stats(**kwargs)


# ---------------------------------------------------------------------------
# Libellés
# ---------------------------------------------------------------------------


def test_french_labels():
    assert fr_day_label("2026-10-10") == "sam. 10 oct."
    assert fr_day_label("2026-10-11") == "dim. 11 oct."
    assert fr_day_label("2026-03-02") == "lun. 2 mars"
    assert fr_day_label("2026-08-12") == "mer. 12 août"
    assert format_duration(1872) == "31 min 12 s"
    assert format_duration(1860) == "31 min"
    assert format_duration(45) == "45 s"
    assert format_duration(None) == "—"
    assert format_signed_lp(28) == "+28 LP"
    assert format_signed_lp(-12) == "-12 LP"
    assert format_signed_lp(0) == "0 LP"
    assert format_signed_lp(None) == "—"
    assert format_signed_lp(3.04, "LP/partie", digits=1) == "+3.0 LP/partie"


def test_label_from_absolute_lp():
    assert label_from_absolute_lp(1445) == "Gold II · 45 LP"
    assert label_from_absolute_lp(2920) == "Master · 120 LP"
    assert label_from_absolute_lp(1444.6) == "Gold II · 45 LP"
    assert label_from_absolute_lp(0) == "Iron IV · 0 LP"
    assert label_from_absolute_lp(None) == "Non classé"
    assert color_from_absolute_lp(1445) == RANK_COLORS["GOLD"]
    assert color_from_absolute_lp(3500) == RANK_COLORS["MASTER"]
    assert color_from_absolute_lp(None) == RANK_COLORS["UNRANKED"]


def test_format_metric():
    assert format_metric(42, "lp", "LP") == "+42 LP"
    assert format_metric(63.2, "pct", "%") == "63 %"
    assert format_metric(1445, "rank") == "Gold II · 45 LP"
    assert format_metric(1872, "duration", "s") == "31 min"
    assert format_metric(-2.345, "dec1", "LP/partie") == "-2.3 LP/partie"
    assert format_metric(3.456, "dec2") == "3.46"
    assert format_metric(6.33, "dec1") == "6.3"
    assert format_metric(15330, "int") == "15 330"
    assert format_metric(None, "int") == "—"


# ---------------------------------------------------------------------------
# PlayerStats : profil détaillé
# ---------------------------------------------------------------------------


def test_profile_totals_and_averages():
    s = compute()
    assert s.games == 4 and s.wins == 3 and s.losses == 1
    assert s.summoner_level == 321
    assert (s.kills, s.deaths, s.assists) == (21, 14, 31)
    assert (s.avg_kills, s.avg_deaths, s.avg_assists) == (round(21 / 4, 1), 3.5, round(31 / 4, 1))
    assert s.avg_kill_participation == 70.0  # (60 + 80) / 2 : parties sans KP ignorées
    assert s.avg_damage_share == 22.5  # (30 + 15) / 2
    assert s.avg_cs == 202.5
    assert s.avg_gold == 11500
    assert s.avg_gold_per_min == round(statistics.fmean([600, 300, 375, 400]), 1)
    assert s.avg_damage_per_min == round(statistics.fmean([1200, 400, 750, 640]))
    assert s.avg_damage_taken == 3750  # 15000 / 4 : NULL compte pour 0
    assert (s.avg_heal, s.avg_cc_time, s.avg_time_dead) == (750, 5, round(30 / 4))
    assert (s.avg_wards_placed, s.avg_wards_killed, s.avg_control_wards) == (4.0, 0.5, 0.5)
    assert (s.double_kills, s.triple_kills, s.quadra_kills, s.penta_kills, s.multikills) == (2, 1, 0, 1, 4)
    assert s.first_bloods == 1 and s.largest_killing_spree == 6 and s.largest_multi_kill == 5
    assert (s.turret_kills, s.dragon_kills, s.baron_kills, s.objectives_stolen, s.surrenders) == (2, 1, 1, 1, 1)
    assert (s.avg_game_duration, s.total_time_played, s.longest_game_s, s.shortest_game_s) == (1725, 6900, 2400, 1200)


def test_profile_lp_and_sides():
    s = compute()
    assert s.lp_net == 38  # Platinum IV 28 (1628) − Gold I 90 (1590)
    assert s.lp_per_game == 9.5
    assert s.lp_known_games == 3
    assert s.avg_lp_win == 21.5 and s.avg_lp_loss == -20.0
    assert s.best_lp_gain == 25 and s.worst_lp_loss == -20
    assert (s.games_blue, s.wins_blue, s.winrate_blue) == (2, 2, 100.0)
    assert (s.games_red, s.wins_red, s.winrate_red) == (1, 0, 0.0)


def test_profile_splits():
    s = compute()
    assert [(e["position"], e["label"], e["games"], e["wins"]) for e in s.by_position] == [
        ("MIDDLE", "Mid", 2, 1), ("UTILITY", "Support", 1, 1), (None, "Inconnu", 1, 1),
    ]
    mid = s.by_position[0]
    assert set(mid) == {"position", "label", "icon_url", "games", "wins", "losses", "winrate", "avg_kda"}
    assert mid["icon_url"].endswith("/svg/position-middle.svg") and s.by_position[2]["icon_url"] is None
    assert mid["winrate"] == 50.0 and mid["avg_kda"] == round((9 + 0.75) / 2, 2)
    assert [(e["label"], e["games"], e["wins"], e["losses"]) for e in s.by_duration] == [
        ("< 25 min", 1, 1, 0), ("25–35 min", 2, 1, 1), ("> 35 min", 1, 1, 0),
    ]
    assert [(e["label"], e["games"]) for e in s.by_hour] == [
        ("Matin (6h–12h)", 1), ("Après-midi (12h–18h)", 1), ("Soirée (18h–24h)", 1), ("Nuit (0h–6h)", 1),
    ]
    assert set(s.by_hour[0]) == {"label", "games", "wins", "losses", "winrate"}
    assert s.by_day == [
        {"day": "2026-10-10", "label": "sam. 10 oct.", "games": 3, "wins": 2, "losses": 1, "winrate": 66.7,
         "lp_change": 5, "limit": 10, "over_quota": 0, "joker": False},
        {"day": "2026-10-11", "label": "dim. 11 oct.", "games": 1, "wins": 1, "losses": 0, "winrate": 100.0,
         "lp_change": 18, "limit": 10, "over_quota": 0, "joker": False},
    ]


def test_profile_records():
    s = compute()
    assert set(s.records) == set(RECORD_KEYS)
    labels = {key: record["label"] for key, record in s.records.items()}
    assert labels == {
        "best_kda": "5/0/15 · KDA parfait",
        "most_kills": "10 kills · 10/2/8",
        "most_assists": "15 assists · 5/0/15",
        "most_damage": "30 000 dégâts",
        "best_cs_per_min": "10.0 CS/min · 200 CS",
        "most_vision": "Score de vision 50",
        "biggest_lp_gain": "+25 LP",
        "longest_game": "40 min",
        "shortest_game": "20 min",
    }
    best = s.records["best_kda"]
    assert set(best) == {"match_id", "champion_name", "champion_icon_url", "champion_splash_url", "value", "label",
                         "win", "game_end", "position"}
    assert best["champion_name"] == "Lux" and best["value"] == 20.0 and best["win"] is True
    assert best["position"] == "UTILITY" and best["game_end"] == "2026-10-10T21:10:00+00:00"
    assert best["champion_icon_url"].endswith("/img/champion/Lux.png")
    assert best["champion_splash_url"].endswith("/splash/Lux_0.jpg")
    assert s.records["best_cs_per_min"]["value"] == 10.0
    assert s.records["shortest_game"]["value"] == 1200


def test_kda_record_label_with_deaths():
    player, snapshots, _ = scenario()
    s = compute(participants=[game(utc(2026, 10, 10, 9, 0), 1800, win=True, kills=12, deaths=2, assists=9)])
    assert s.records["best_kda"]["label"] == "12/2/9 · KDA 10.5"
    assert s.records["biggest_lp_gain"] is None  # aucune variation de LP connue


def test_profile_season_and_rank_progress():
    s = compute()
    assert (s.season_wins, s.season_losses, s.season_winrate) == (53, 41, 56.4)
    assert s.peak_absolute_lp == 1628 and s.peak_rank_label == "Platinum IV · 28 LP"
    assert s.low_absolute_lp == 1590 and s.low_rank_label == "Gold I · 90 LP"  # le Silver d'avant est hors fenêtre
    assert (s.promotions, s.demotions) == (2, 1)
    assert s.rank_delta_lp == 38


def test_rank_progress_ignores_unranked_gaps_and_apex():
    snapshots = [
        snap(utc(2026, 10, 10, 1), "DIAMOND", "I", 90),
        snap(utc(2026, 10, 10, 2), "MASTER", None, 10),  # promotion
        snap(utc(2026, 10, 10, 3), "MASTER", None, 60),  # même échelon
        snap(utc(2026, 10, 10, 4), None, None, 0),  # Unranked : ignoré
        snap(utc(2026, 10, 10, 5), "GRANDMASTER", None, 200),
    ]
    progress = rank_progress(snapshots)
    assert (progress["promotions"], progress["demotions"]) == (1, 0)
    assert progress["peak_rank_label"] == "Grandmaster · 200 LP" and progress["peak_absolute_lp"] == 3000
    assert progress["low_rank_label"] == "Diamond I · 90 LP"
    assert rank_progress([]) == {"peak_absolute_lp": None, "peak_rank_label": None, "low_absolute_lp": None,
                                 "low_rank_label": None, "promotions": 0, "demotions": 0}


def test_window_end_bounds_rank_progress():
    s = compute(window_end=utc(2026, 10, 10, 14, 0))
    assert s.games == 2
    assert s.peak_absolute_lp == 1615 and (s.promotions, s.demotions) == (1, 1)  # snapshots ≤ fin + 10 min
    assert s.rank_delta_lp == 38  # rang actuel (dernier snapshot) − référence


def test_profile_without_games():
    s = compute(participants=[])
    for attr in ("kills", "avg_kills", "avg_kill_participation", "avg_gold", "multikills", "penta_kills",
                 "surrenders", "avg_game_duration", "lp_per_game", "lp_known_games", "avg_lp_win",
                 "best_lp_gain", "games_blue", "winrate_red", "longest_game_s"):
        assert getattr(s, attr) is None, attr
    assert [e["games"] for e in s.by_duration] == [0, 0, 0]
    assert [e["games"] for e in s.by_hour] == [0, 0, 0, 0]
    assert s.by_position == [] and s.by_day == []
    assert s.records == {key: None for key in RECORD_KEYS}
    assert s.season_wins == 53  # le rang reste connu
    json.dumps(s.to_dict())


def test_profile_unranked():
    s = compute(snapshots=[])
    assert s.season_wins is None and s.season_losses is None and s.season_winrate is None
    assert s.peak_absolute_lp is None and s.low_rank_label is None
    assert (s.promotions, s.demotions) == (0, 0)
    assert s.rank_delta_lp is None
    assert s.lp_per_game == 0.0  # 0 LP net sur 4 parties


def test_champion_entries_have_profile_fields():
    s = compute()
    by_name = {c["champion_name"]: c for c in s.champions}
    ahri = by_name["Ahri"]
    required = {"champion_name", "champion_id", "icon_url", "splash_url", "loading_url", "games", "wins", "losses",
                "winrate", "avg_kda", "avg_kills", "avg_deaths", "avg_assists", "avg_cs_per_min", "avg_damage",
                "avg_kill_participation", "lp_change", "last_played"}
    assert required <= set(ahri)
    assert ahri["champion_id"] == 103 and ahri["games"] == 2
    assert (ahri["avg_kills"], ahri["avg_deaths"], ahri["avg_assists"]) == (6.0, 5.0, 6.0)
    assert ahri["avg_damage"] == 18000 and ahri["avg_kill_participation"] == 60.0
    assert ahri["lp_change"] == 5 and ahri["last_played"] == "2026-10-10T12:30:00+00:00"
    assert by_name["Lux"]["lp_change"] is None and by_name["Lux"]["champion_id"] == 99
    assert by_name["Zed"]["champion_id"] is None and by_name["Zed"]["avg_kill_participation"] is None


def test_partner_record():
    s = make_stats(games=10, wins=6, losses=4)
    record = partner_record(s, partner_id=2, display_name="Léa", icon_url="https://x/1.png", together=(4, 3, 1))
    assert record == {
        "player_id": 2, "display_name": "Léa", "icon_url": "https://x/1.png",
        "together_games": 4, "together_wins": 3, "together_losses": 1, "together_winrate": 75.0,
        "solo_games": 6, "solo_wins": 3, "solo_losses": 3, "solo_winrate": 50.0,
    }
    alone = partner_record(make_stats(), partner_id=2, display_name="Léa", icon_url=None, together=(0, 0, 0))
    assert alone["together_winrate"] is None and alone["solo_games"] == 0 and alone["solo_winrate"] is None


def test_player_to_dict_is_json_with_all_fields():
    d = compute().to_dict()
    json.dumps(d)
    assert set(d) == set(PlayerStats.__dataclass_fields__)
    assert d["partner"] is None  # rempli par app.api.leaderboard


# ---------------------------------------------------------------------------
# TeamStats
# ---------------------------------------------------------------------------


def _member(player_id: int, **overrides) -> PlayerStats:
    return make_stats(**{"player_id": player_id, "display_name": f"J{player_id}", **overrides})


def test_team_profile_fields():
    team = Team(id=1, name="Duo Rouge", color="#ef4444", slot=1)
    a = _member(1, absolute_lp=1500, rank_label="Gold III · 0 LP", lp_net=40, games=6, wins=4, losses=2,
                kills=30, deaths=20, assists=40, avg_kill_participation=50.0, best_win_streak=3, multikills=2,
                penta_kills=0, first_bloods=1, dragon_kills=2, baron_kills=1, turret_kills=4, objectives_stolen=0,
                surrenders=1, total_time_played=10800, avg_damage_share=20.0, avg_gold_per_min=400.0,
                avg_wards_placed=10.0, season_wins=50, season_losses=40)
    b = _member(2, absolute_lp=1700, rank_label="Platinum II · 0 LP", lp_net=-10, games=4, wins=1, losses=3,
                kills=10, deaths=15, assists=12, avg_kill_participation=41.0, best_win_streak=1,
                total_time_played=7200, avg_gold_per_min=451.0)
    t = compute_team_stats(team, [a, b])
    assert t.avg_absolute_lp == 1600 and t.rank_label == "Platinum IV · 0 LP"
    assert t.rank_color == RANK_COLORS["PLATINUM"]
    assert t.top_player_id == 2 and t.top_player_rank_label == "Platinum II · 0 LP"
    assert (t.kills, t.deaths, t.assists) == (40, 35, 52)
    assert t.avg_kill_participation == 45.5
    assert t.lp_per_game == 3.0
    assert t.best_win_streak == 3
    assert (t.multikills, t.penta_kills, t.first_bloods, t.dragon_kills) == (2, 0, 1, 2)
    assert (t.baron_kills, t.turret_kills, t.objectives_stolen, t.surrenders) == (1, 4, 0, 1)
    assert t.total_time_played == 18000 and t.avg_game_duration == 1800
    assert t.avg_damage_share == 20.0 and t.avg_gold_per_min == 425.5 and t.avg_wards_placed == 10.0
    assert (t.season_wins, t.season_losses, t.season_winrate) == (50, 40, 55.6)
    d = t.to_dict()
    json.dumps(d)
    assert set(d) == set(TeamStats.__dataclass_fields__)


def test_team_profile_without_rank_or_games():
    team = Team(id=2, name="Duo Vide", color="#000000", slot=2)
    t = compute_team_stats(team, [_member(1, absolute_lp=None), _member(2, absolute_lp=None)])
    assert t.avg_absolute_lp is None and t.rank_label == "Non classé" and t.rank_color == RANK_COLORS["UNRANKED"]
    assert t.top_player_id is None and t.top_player_rank_label is None
    assert t.kills is None and t.multikills is None and t.lp_per_game is None and t.avg_game_duration is None
    assert t.season_wins is None and t.season_winrate is None and t.best_win_streak == 0


# ---------------------------------------------------------------------------
# compare_teams
# ---------------------------------------------------------------------------

EXPECTED_METRICS = [
    "lp_net", "lp_per_game", "winrate", "games", "together_games", "together_winrate", "avg_absolute_lp",
    "avg_kda", "avg_kill_participation", "avg_cs_per_min", "avg_gold_per_min", "avg_damage", "avg_damage_share",
    "avg_vision", "avg_wards_placed", "best_win_streak", "multikills", "penta_kills", "first_bloods",
    "dragon_kills", "baron_kills", "turret_kills", "objectives_stolen", "avg_game_duration", "surrenders",
    "surrender_wins", "season_winrate",
]


def test_compare_teams():
    t1 = make_team_stats(1, lp_net=42, wins=5, losses=3)
    t2 = make_team_stats(2, lp_net=-8, wins=2, losses=2)
    t3 = make_team_stats(3, lp_net=10, wins=1, losses=1)
    t1.avg_absolute_lp, t2.avg_absolute_lp, t3.avg_absolute_lp = 1445, 1200, None
    t1.surrenders, t2.surrenders, t3.surrenders = 3, 1, 2
    t1.avg_game_duration, t2.avg_game_duration = 1872, 1700
    t1.penta_kills = t2.penta_kills = t3.penta_kills = 0
    t1.multikills = 4
    data = compare_teams([t1, t2, t3])
    metrics = {m["key"]: m for m in data["metrics"]}
    assert [m["key"] for m in data["metrics"]] == EXPECTED_METRICS == [m[0] for m in COMPARISON_METRICS]
    for metric in data["metrics"]:
        assert set(metric) == {"key", "label", "unit", "higher_is_better", "format", "values", "best_team_id",
                               "worst_team_id"}
        assert metric["unit"] in ("", "%", "LP", "s", "LP/partie")
        assert metric["format"] in ("int", "dec1", "dec2", "pct", "lp", "duration", "rank")
        assert [v["team_id"] for v in metric["values"]] == [1, 2, 3]
        assert all(set(v) == {"team_id", "value", "display"} for v in metric["values"])
    lp = metrics["lp_net"]
    assert [v["display"] for v in lp["values"]] == ["+42 LP", "-8 LP", "+10 LP"]
    assert (lp["best_team_id"], lp["worst_team_id"], lp["unit"], lp["format"]) == (1, 2, "LP", "lp")
    winrate_metric = metrics["winrate"]
    assert [v["display"] for v in winrate_metric["values"]] == ["62 %", "50 %", "50 %"]
    assert (winrate_metric["best_team_id"], winrate_metric["worst_team_id"]) == (1, 2)  # égalité → le premier
    rank = metrics["avg_absolute_lp"]
    assert rank["label"] == "Rang moyen" and rank["format"] == "rank"
    assert [v["display"] for v in rank["values"]] == ["Gold II · 45 LP", "Gold IV · 0 LP", "—"]
    assert (rank["best_team_id"], rank["worst_team_id"]) == (1, 2)
    surrenders = metrics["surrenders"]
    assert surrenders["higher_is_better"] is False and (surrenders["best_team_id"], surrenders["worst_team_id"]) == (2, 1)
    duration = metrics["avg_game_duration"]
    assert duration["higher_is_better"] is False and duration["unit"] == "s"
    assert [v["display"] for v in duration["values"]] == ["31 min", "28 min", "—"]
    assert (duration["best_team_id"], duration["worst_team_id"]) == (2, 1)
    # Toutes égales, ou une seule valeur connue → ni meilleur ni pire
    assert (metrics["penta_kills"]["best_team_id"], metrics["penta_kills"]["worst_team_id"]) == (None, None)
    assert (metrics["multikills"]["best_team_id"], metrics["multikills"]["worst_team_id"]) == (None, None)
    assert metrics["lp_per_game"]["values"][0]["value"] is None  # make_team_stats sans lp_per_game
    json.dumps(data)


def test_compare_teams_empty():
    assert all(m["values"] == [] and m["best_team_id"] is None for m in compare_teams([])["metrics"])


# ---------------------------------------------------------------------------
# build_rank_ladder
# ---------------------------------------------------------------------------


def _ladder_fixture():
    team1 = Team(id=1, name="Duo Rouge", color="#ef4444", slot=1)
    team2 = Team(id=2, name="Duo Bleu", color="#3b82f6", slot=2)
    lea = _member(1, display_name="Léa", team_id=1, tier="DIAMOND", rank="II", lp=47, absolute_lp=2647,
                  rank_label="Diamond II · 47 LP", rank_color=RANK_COLORS["DIAMOND"], baseline_absolute_lp=2500,
                  lp_net=147, season_wins=72, season_losses=63, season_winrate=53.3, peak_rank_label="Diamond II · 60 LP",
                  promotions=2, demotions=1, rank_delta_lp=147, games=12, winrate=58.3)
    mike = _member(2, display_name="Mike", team_id=1, tier="GOLD", rank="I", lp=10, absolute_lp=1610,
                   rank_label="Gold I · 10 LP", baseline_absolute_lp=2600, lp_net=-990)
    sam = _member(3, display_name="Sam", team_id=2, tier="GOLD", rank="I", lp=10, absolute_lp=1610,
                  rank_label="Gold I · 10 LP", baseline_absolute_lp=1700, lp_net=-90)
    zoe = _member(4, display_name="Zoé", team_id=2, tier=None, rank=None, absolute_lp=None, rank_label="Unranked",
                  rank_color=RANK_COLORS["UNRANKED"], baseline_absolute_lp=None, is_linked=False)
    hugo = _member(5, display_name="Hugo", team_id=None, tier="MASTER", rank=None, lp=120, absolute_lp=2920,
                   rank_label="Master · 120 LP", baseline_absolute_lp=None, lp_net=0)
    anna = _member(6, display_name="anna", team_id=None, tier=None, rank=None, absolute_lp=None,
                   rank_label="Unranked", baseline_absolute_lp=None)
    t1 = compute_team_stats(team1, [lea, mike])
    t2 = compute_team_stats(team2, [sam, zoe])
    return [t1, t2], [hugo, anna, lea]  # Léa en double : ignorée


LADDER_KEYS = {
    "position", "player_id", "display_name", "icon_url", "summoner_level", "team_id", "team_name", "team_color",
    "tier", "rank", "lp", "rank_label", "rank_color", "rank_emblem_url", "rank_crest_url", "absolute_lp",
    "baseline_absolute_lp", "baseline_rank_label", "baseline_position", "position_delta", "lp_net",
    "rank_delta_lp", "season_wins", "season_losses", "season_winrate", "hot_streak", "peak_rank_label",
    "promotions", "demotions", "is_linked", "live", "games", "winrate",
}


def test_build_rank_ladder_players():
    teams, unassigned = _ladder_fixture()
    ladder = build_rank_ladder(teams, unassigned)
    assert set(ladder) == {"players", "teams", "tiers", "summary"}
    players = ladder["players"]
    assert all(set(p) == LADDER_KEYS for p in players)
    # Absolu desc ; égalité Mike / Sam départagée par LP nets ; non classés à la fin par pseudo
    assert [(p["position"], p["display_name"]) for p in players] == [
        (1, "Hugo"), (2, "Léa"), (3, "Sam"), (4, "Mike"), (None, "anna"), (None, "Zoé"),
    ]
    hugo, lea, sam, mike, anna, zoe = players
    assert lea["team_name"] == "Duo Rouge" and lea["team_color"] == "#ef4444" and lea["team_id"] == 1
    assert hugo["team_id"] is None and hugo["team_name"] is None and hugo["team_color"] is None
    # Positions de référence : Mike 2600, Léa 2500, Sam 1700 (Hugo sans référence)
    assert (mike["baseline_position"], mike["position_delta"]) == (1, -3)
    assert (lea["baseline_position"], lea["position_delta"]) == (2, 0)
    assert (sam["baseline_position"], sam["position_delta"]) == (3, 0)
    assert hugo["baseline_position"] is None and hugo["position_delta"] is None
    assert lea["baseline_rank_label"] == "Diamond III · 0 LP" and hugo["baseline_rank_label"] == "Non classé"
    assert (lea["season_wins"], lea["season_winrate"], lea["peak_rank_label"]) == (72, 53.3, "Diamond II · 60 LP")
    assert (lea["promotions"], lea["demotions"], lea["rank_delta_lp"]) == (2, 1, 147)
    assert zoe["rank_label"] == "Non classé" and zoe["tier"] is None and zoe["is_linked"] is False
    assert zoe["position_delta"] is None
    json.dumps(ladder)


def test_build_rank_ladder_teams_tiers_summary():
    teams, unassigned = _ladder_fixture()
    ladder = build_rank_ladder(teams, unassigned)
    assert ladder["teams"] == [
        {"position": 1, "team_id": 1, "name": "Duo Rouge", "color": "#ef4444", "avg_absolute_lp": 2128,
         "rank_label": "Emerald III · 28 LP", "rank_color": RANK_COLORS["EMERALD"], "top_player_id": 1,
         "top_player_rank_label": "Diamond II · 47 LP", "players": [1, 2], "lp_net": -843},
        {"position": 2, "team_id": 2, "name": "Duo Bleu", "color": "#3b82f6", "avg_absolute_lp": 1610,
         "rank_label": "Platinum IV · 10 LP", "rank_color": RANK_COLORS["PLATINUM"], "top_player_id": 3,
         "top_player_rank_label": "Gold I · 10 LP", "players": [3, 4], "lp_net": -90},
    ]
    assert ladder["tiers"] == [
        {"tier": "MASTER", "label": "Maître", "color": RANK_COLORS["MASTER"], "count": 1, "players": ["Hugo"]},
        {"tier": "DIAMOND", "label": "Diamant", "color": RANK_COLORS["DIAMOND"], "count": 1, "players": ["Léa"]},
        {"tier": "GOLD", "label": "Or", "color": RANK_COLORS["GOLD"], "count": 2, "players": ["Sam", "Mike"]},
        {"tier": "UNRANKED", "label": "Non classé", "color": RANK_COLORS["UNRANKED"], "count": 2,
         "players": ["anna", "Zoé"]},
    ]
    assert ladder["summary"] == {
        "ranked_players": 4,
        "unranked_players": 2,
        "highest": {"player_id": 5, "display_name": "Hugo", "rank_label": "Master · 120 LP"},
        "lowest": {"player_id": 2, "display_name": "Mike", "rank_label": "Gold I · 10 LP"},
        "avg_absolute_lp": round((2920 + 2647 + 1610 + 1610) / 4),
        "avg_rank_label": label_from_absolute_lp(round((2920 + 2647 + 1610 + 1610) / 4)),
    }


def test_build_rank_ladder_empty_and_unranked_team():
    assert build_rank_ladder([], []) == {
        "players": [], "teams": [], "tiers": [],
        "summary": {"ranked_players": 0, "unranked_players": 0, "highest": None, "lowest": None,
                    "avg_absolute_lp": None, "avg_rank_label": "Non classé"},
    }
    team = compute_team_stats(Team(id=9, name="Duo Gris", color="#999999", slot=9), [_member(1, absolute_lp=None)])
    ladder = build_rank_ladder([team], [])
    assert ladder["teams"][0]["position"] is None and ladder["teams"][0]["rank_label"] == "Non classé"


# ---------------------------------------------------------------------------
# metric_rankings
# ---------------------------------------------------------------------------


def test_metric_rankings_competition_and_nulls():
    players = [
        _member(1, absolute_lp=1500, avg_kda=3.0, avg_deaths=4.0, penta_kills=None, games=10),
        _member(2, absolute_lp=1800, avg_kda=3.0, avg_deaths=6.0, penta_kills=1, games=8),
        _member(3, absolute_lp=None, avg_kda=4.5, avg_deaths=3.0, penta_kills=0, games=12),
        _member(4, absolute_lp=2500, avg_kda=9.0, avg_deaths=1.0, penta_kills=5, games=40, is_linked=False),
        _member(5, absolute_lp=2600, avg_kda=9.0, avg_deaths=1.0, penta_kills=5, games=40, active=False),
    ]
    rankings = metric_rankings(players, 1)
    assert list(rankings) == [key for key, _ in RANKING_METRICS]
    assert rankings["absolute_lp"] == {"position": 2, "total": 2, "value": 1500, "higher_is_better": True}
    assert rankings["avg_kda"] == {"position": 2, "total": 3, "value": 3.0, "higher_is_better": True}  # 1, 2, 2
    assert metric_rankings(players, 2)["avg_kda"]["position"] == 2  # égalité : même position
    assert rankings["avg_deaths"] == {"position": 2, "total": 3, "value": 4.0, "higher_is_better": False}
    assert rankings["penta_kills"] == {"position": None, "total": 2, "value": None, "higher_is_better": True}
    assert rankings["games"]["position"] == 2
    # Joueur non lié : valeur connue, sans position ; joueur inconnu : rien
    assert metric_rankings(players, 4)["avg_kda"] == {"position": None, "total": 3, "value": 9.0,
                                                      "higher_is_better": True}
    assert metric_rankings(players, 99)["games"]["value"] is None
