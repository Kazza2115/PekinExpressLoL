"""Tests unitaires des calculs purs (`app.services.stats`) — aucune base de données."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.db.models import MatchParticipant, Player, Queue, RankSnapshot, Team
from app.services import stats
from app.services.stats import (
    DIVISIONS,
    RANK_COLORS,
    TIERS,
    PlayerStats,
    StreakInfo,
    TeamStats,
    absolute_lp,
    compute_player_stats,
    compute_streak,
    compute_team_stats,
    day_key,
    format_rank,
    kda,
    rank_color,
    rank_from_absolute_lp,
    rank_teams,
    sort_players,
    winrate,
)
from app.state import LiveGameState

PARIS = ZoneInfo("Europe/Paris")
UTC = timezone.utc


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# ---------------------------------------------------------------------------
# absolute_lp / rank_from_absolute_lp / format_rank / rank_color
# ---------------------------------------------------------------------------


class TestAbsoluteLp:
    def test_iron_iv_zero_is_origin(self):
        assert absolute_lp("IRON", "IV", 0) == 0

    def test_division_is_100_lp(self):
        assert absolute_lp("IRON", "III", 0) == 100
        assert absolute_lp("IRON", "I", 99) == 399

    def test_tier_is_400_lp(self):
        assert absolute_lp("BRONZE", "IV", 0) == 400
        assert absolute_lp("GOLD", "IV", 0) == 1200
        assert absolute_lp("DIAMOND", "IV", 0) == 2400

    def test_gold_ii_45(self):
        # tier GOLD (index 3) → 1200 ; division II (index 2) → +200 ; +45 LP
        assert absolute_lp("GOLD", "II", 45) == 1445

    def test_diamond_i_100_touches_master(self):
        assert absolute_lp("DIAMOND", "I", 100) == 2800
        assert absolute_lp("MASTER", None, 0) == 2800

    def test_apex_tiers_share_base(self):
        assert absolute_lp("MASTER", None, 120) == 2920
        assert absolute_lp("MASTER", "I", 120) == 2920
        assert absolute_lp("GRANDMASTER", "I", 500) == 3300
        assert absolute_lp("CHALLENGER", None, 1200) == 4000

    def test_unranked_is_none(self):
        assert absolute_lp(None, None, 0) is None
        assert absolute_lp("UNRANKED", None, 0) is None
        assert absolute_lp("", None, 0) is None
        assert absolute_lp("unranked", "IV", 10) is None

    def test_unknown_tier_is_none(self):
        assert absolute_lp("WOOD", "IV", 10) is None

    def test_case_insensitive(self):
        assert absolute_lp("gold", "ii", 45) == 1445
        assert absolute_lp("Gold", "Ii", 45) == 1445
        assert absolute_lp("master", None, 7) == 2807

    def test_missing_division_defaults_to_lowest(self):
        assert absolute_lp("GOLD", None, 10) == 1210


class TestRankFromAbsoluteLp:
    @pytest.mark.parametrize("tier", TIERS[:7])
    @pytest.mark.parametrize("division", DIVISIONS)
    @pytest.mark.parametrize("lp", [0, 45, 99])
    def test_round_trip_all_tiers(self, tier: str, division: str, lp: int):
        value = absolute_lp(tier, division, lp)
        assert value is not None
        assert rank_from_absolute_lp(value) == (tier, division, lp)

    @pytest.mark.parametrize("lp", [0, 1, 120, 999])
    def test_round_trip_master(self, lp: int):
        value = absolute_lp("MASTER", None, lp)
        assert value is not None
        assert rank_from_absolute_lp(value) == ("MASTER", None, lp)

    def test_apex_collapse_to_master(self):
        # Grandmaster / Challenger partagent la base → l'inverse renvoie toujours MASTER
        assert rank_from_absolute_lp(absolute_lp("GRANDMASTER", None, 50)) == ("MASTER", None, 50)
        assert rank_from_absolute_lp(absolute_lp("CHALLENGER", None, 1000)) == ("MASTER", None, 1000)

    def test_boundaries(self):
        assert rank_from_absolute_lp(0) == ("IRON", "IV", 0)
        assert rank_from_absolute_lp(99) == ("IRON", "IV", 99)
        assert rank_from_absolute_lp(100) == ("IRON", "III", 0)
        assert rank_from_absolute_lp(399) == ("IRON", "I", 99)
        assert rank_from_absolute_lp(400) == ("BRONZE", "IV", 0)
        assert rank_from_absolute_lp(1445) == ("GOLD", "II", 45)
        assert rank_from_absolute_lp(2799) == ("DIAMOND", "I", 99)
        assert rank_from_absolute_lp(2800) == ("MASTER", None, 0)

    def test_negative_clamped_to_zero(self):
        assert rank_from_absolute_lp(-50) == ("IRON", "IV", 0)

    def test_demotion_and_promotion_via_absolute(self):
        # Gold IV 10 LP − 20 → Silver I 90 LP ; Gold I 95 + 20 → Platinum IV 15
        assert rank_from_absolute_lp(absolute_lp("GOLD", "IV", 10) - 20) == ("SILVER", "I", 90)
        assert rank_from_absolute_lp(absolute_lp("GOLD", "I", 95) + 20) == ("PLATINUM", "IV", 15)


class TestFormatRank:
    def test_division_tier(self):
        assert format_rank("GOLD", "II", 45) == "Gold II · 45 LP"
        assert format_rank("iron", "iv", 0) == "Iron IV · 0 LP"

    def test_apex(self):
        assert format_rank("MASTER", None, 120) == "Master · 120 LP"
        assert format_rank("MASTER", "I", 120) == "Master · 120 LP"
        assert format_rank("GRANDMASTER", "I", 400) == "Grandmaster · 400 LP"
        assert format_rank("CHALLENGER", None, 1300) == "Challenger · 1300 LP"

    def test_unranked(self):
        assert format_rank(None, None, 0) == "Unranked"
        assert format_rank("UNRANKED", None, 0) == "Unranked"


class TestRankColor:
    def test_palette_values(self):
        assert RANK_COLORS["IRON"] == "#8a8a8a"
        assert RANK_COLORS["BRONZE"] == "#b07a4a"
        assert RANK_COLORS["SILVER"] == "#a9b4c0"
        assert RANK_COLORS["GOLD"] == "#e5b64d"
        assert RANK_COLORS["PLATINUM"] == "#4fb8a8"
        assert RANK_COLORS["EMERALD"] == "#3fbf7f"
        assert RANK_COLORS["DIAMOND"] == "#5aa0ff"
        assert RANK_COLORS["MASTER"] == "#b465f0"
        assert RANK_COLORS["GRANDMASTER"] == "#f05a5a"
        assert RANK_COLORS["CHALLENGER"] == "#7fe0ff"
        assert RANK_COLORS["UNRANKED"] == "#6b7280"

    def test_lookup(self):
        assert rank_color("GOLD") == "#e5b64d"
        assert rank_color("gold") == "#e5b64d"
        assert rank_color(None) == "#6b7280"
        assert rank_color("UNRANKED") == "#6b7280"
        assert rank_color("WOOD") == "#6b7280"


# ---------------------------------------------------------------------------
# compute_streak / kda / winrate / day_key
# ---------------------------------------------------------------------------


class TestStreak:
    def test_empty(self):
        info = compute_streak([])
        assert info == StreakInfo(kind=None, length=0, best_win=0, best_loss=0)
        assert info.label == "—"

    def test_current_streak_is_tail(self):
        info = compute_streak([True, False, True, True, True])
        assert info.kind == "W"
        assert info.length == 3
        assert info.label == "W3"

    def test_loss_streak(self):
        info = compute_streak([True, True, False, False])
        assert info.label == "L2"
        assert info.best_win == 2
        assert info.best_loss == 2

    def test_best_streaks_are_longest_runs(self):
        results = [True, True, True, False, True, False, False, False, False, True]
        info = compute_streak(results)
        assert info.best_win == 3
        assert info.best_loss == 4
        assert info.label == "W1"

    def test_single_game(self):
        assert compute_streak([False]).label == "L1"


class TestKdaWinrate:
    def test_kda(self):
        assert kda(10, 2, 5) == 7.5
        assert kda(3, 0, 4) == 7.0  # sans mort → K + A
        assert kda(0, 5, 0) == 0.0

    def test_winrate(self):
        assert winrate(0, 0) is None
        assert winrate(3, 1) == 75.0
        assert winrate(1, 2) == pytest.approx(33.3)
        assert winrate(0, 4) == 0.0
        assert winrate(5, 0) == 100.0


class TestDayKey:
    def test_paris_around_midnight(self):
        # Octobre : Paris = UTC+2 → 22:30 UTC est déjà le lendemain à Paris
        assert day_key(utc(2026, 10, 10, 21, 30), PARIS) == "2026-10-10"
        assert day_key(utc(2026, 10, 10, 22, 30), PARIS) == "2026-10-11"
        assert day_key(utc(2026, 10, 10, 21, 59, 59), PARIS) == "2026-10-10"
        assert day_key(utc(2026, 10, 10, 22, 0, 0), PARIS) == "2026-10-11"

    def test_winter_time(self):
        # Janvier : Paris = UTC+1
        assert day_key(utc(2026, 1, 10, 22, 30), PARIS) == "2026-01-10"
        assert day_key(utc(2026, 1, 10, 23, 30), PARIS) == "2026-01-11"

    def test_naive_datetime_is_utc(self):
        assert day_key(datetime(2026, 10, 10, 22, 30), PARIS) == "2026-10-11"

    def test_utc_key(self):
        assert day_key(utc(2026, 10, 10, 23, 59), UTC) == "2026-10-10"


# ---------------------------------------------------------------------------
# compute_player_stats
# ---------------------------------------------------------------------------


def make_player(**overrides) -> Player:
    data = dict(
        id=1,
        display_name="Mike",
        game_name="Mike",
        tag_line="EUW",
        puuid="puuid-mike",
        profile_icon_id=5,
        team_id=1,
        active=True,
    )
    data.update(overrides)
    return Player(**data)


def snap(captured_at: datetime, tier: str | None, rank: str | None, lp: int, **overrides) -> RankSnapshot:
    data = dict(player_id=1, queue=Queue.SOLO, tier=tier, rank=rank, lp=lp, captured_at=captured_at)
    data.update(overrides)
    return RankSnapshot(**data)


_match_counter = 0


def game(
    game_start: datetime,
    *,
    win: bool,
    champion: str = "Ahri",
    kills: int = 5,
    deaths: int = 5,
    assists: int = 5,
    cs: int = 180,
    duration: int = 1800,
    vision: int = 20,
    damage: int = 15000,
    **overrides,
) -> MatchParticipant:
    global _match_counter
    _match_counter += 1
    data = dict(
        match_id=f"EUW1_{_match_counter}",
        player_id=1,
        queue=Queue.SOLO,
        game_start=game_start,
        game_duration=duration,
        champion_name=champion,
        win=win,
        kills=kills,
        deaths=deaths,
        assists=assists,
        cs=cs,
        vision_score=vision,
        damage_to_champions=damage,
        is_remake=False,
    )
    data.update(overrides)
    return MatchParticipant(**data)


WINDOW_START = utc(2026, 10, 10, 0, 0)
NOW = utc(2026, 10, 10, 23, 0)  # 01:00 le 11 octobre à Paris


def scenario() -> tuple[Player, list[RankSnapshot], list[MatchParticipant]]:
    """Scénario de référence : référence avant la fenêtre, parties hors fenêtre / remakes / flex."""
    player = make_player()
    snapshots = [
        snap(utc(2026, 10, 8, 12, 0), "GOLD", "IV", 50),  # ancien
        snap(utc(2026, 10, 9, 23, 0), "GOLD", "IV", 80),  # référence (dernier ≤ début)
        snap(utc(2026, 10, 10, 10, 0), "GOLD", "III", 10),
        snap(utc(2026, 10, 10, 19, 0), "GOLD", "III", 40, hot_streak=True),  # dernier SOLO
        snap(utc(2026, 10, 10, 19, 30), "PLATINUM", "IV", 0, queue=Queue.FLEX),  # ignoré
    ]
    participants = [
        game(utc(2026, 10, 9, 20, 0), win=True, champion="Ahri"),  # avant la fenêtre
        game(utc(2026, 10, 10, 8, 0), win=True, champion="Ahri", kills=10, deaths=2, assists=5, cs=200, duration=1800, vision=30, damage=20000),
        game(utc(2026, 10, 10, 9, 0), win=False, champion="Ahri", kills=3, deaths=6, assists=7, cs=150, duration=1500, vision=10, damage=10000),
        game(utc(2026, 10, 10, 9, 40), win=False, champion="Zed", duration=180, is_remake=True),  # remake
        game(utc(2026, 10, 10, 18, 0), win=True, champion="Zed", kills=7, deaths=0, assists=3, cs=240, duration=2400, vision=20, damage=25000),
        game(utc(2026, 10, 10, 19, 0), win=True, champion="Zed", kills=5, deaths=5, assists=5, cs=180, duration=1800, vision=20, damage=15000),
        game(utc(2026, 10, 10, 20, 0), win=True, champion="Jinx", queue=Queue.FLEX),  # flex : ignoré
        # 22:30 UTC = 00:30 à Paris le 11 → compte pour « aujourd'hui » (NOW = 01:00 Paris le 11)
        game(utc(2026, 10, 10, 22, 30), win=True, champion="Zed", kills=2, deaths=2, assists=2, cs=120, duration=1200, vision=20, damage=5000),
    ]
    return player, snapshots, participants


def compute(**overrides) -> PlayerStats:
    player, snapshots, participants = scenario()
    kwargs = dict(
        player=player,
        snapshots=snapshots,
        participants=participants,
        window_start=WINDOW_START,
        window_end=None,
        games_limit=10,
        tz=PARIS,
        now=NOW,
    )
    kwargs.update(overrides)
    return compute_player_stats(**kwargs)


class TestComputePlayerStats:
    def test_identity_fields(self):
        s = compute()
        assert s.player_id == 1
        assert s.display_name == "Mike"
        assert s.riot_id == "Mike#EUW"
        assert s.is_linked is True
        assert s.active is True
        assert s.team_id == 1
        assert s.profile_icon_id == 5
        assert s.games_limit == 10
        # Data Dragon est optionnel à l'exécution : None ou une URL
        assert s.icon_url is None or s.icon_url.startswith("http")

    def test_rank_from_latest_solo_snapshot(self):
        s = compute()
        assert (s.tier, s.rank, s.lp) == ("GOLD", "III", 40)
        assert s.rank_label == "Gold III · 40 LP"
        assert s.rank_color == RANK_COLORS["GOLD"]
        assert s.hot_streak is True
        assert s.absolute_lp == 1340

    def test_baseline_is_last_snapshot_before_window(self):
        s = compute()
        assert s.baseline_absolute_lp == 1280
        assert s.lp_net == 60

    def test_baseline_is_first_snapshot_when_none_before(self):
        s = compute(window_start=utc(2026, 10, 1))
        assert s.baseline_absolute_lp == 1250  # premier snapshot après le début
        assert s.lp_net == 1340 - 1250

    def test_baseline_without_window_is_first_snapshot(self):
        s = compute(window_start=None)
        assert s.baseline_absolute_lp == 1250
        assert s.lp_net == 90

    def test_lp_net_frozen_at_window_end(self):
        # Après la fin de fenêtre, les snapshots suivants ne comptent plus pour les LP nets
        s = compute(window_end=utc(2026, 10, 10, 12, 0))
        assert s.absolute_lp == 1340  # rang actuel affiché
        assert s.lp_net == 1310 - 1280

    def test_games_filtering(self):
        s = compute()
        # 5 parties SOLO dans la fenêtre, hors remake et hors flex
        assert s.games == 5
        assert s.wins == 4
        assert s.losses == 1
        assert s.winrate == 80.0

    def test_window_end_excludes_later_games(self):
        # Une partie compte si elle se TERMINE dans la fenêtre : celle de 18:00 (40 min) finit à 18:40
        s = compute(window_end=utc(2026, 10, 10, 18, 30))
        assert s.games == 2
        assert s.last_game_at == "2026-10-10T09:25:00+00:00"
        s = compute(window_end=utc(2026, 10, 10, 18, 45))
        assert s.games == 3
        assert s.last_game_at == "2026-10-10T18:40:00+00:00"

    def test_window_start_uses_game_end(self):
        # Partie commencée avant « Démarrer » mais terminée après : elle compte (LP appliqués à la fin)
        s = compute(window_start=utc(2026, 10, 10, 8, 15))
        assert s.games == 5
        s = compute(window_start=utc(2026, 10, 10, 8, 31))
        assert s.games == 4

    def test_games_per_day_and_today_in_paris(self):
        s = compute()
        assert s.games_per_day == {"2026-10-10": 4, "2026-10-11": 1}
        assert s.games_today == 1  # il est 01:00 le 11 à Paris

    def test_games_today_earlier_in_the_day(self):
        s = compute(now=utc(2026, 10, 10, 20, 0))  # 22:00 à Paris le 10
        assert s.games_today == 4

    def test_streak(self):
        s = compute()
        # W L W W W (chronologique)
        assert s.streak == "W3"
        assert s.best_win_streak == 3
        assert s.best_loss_streak == 1

    def test_averages(self):
        s = compute()
        # KDA : 7.5, 1.667, 10 (0 mort), 2, 2 → 4.63
        assert s.avg_kda == pytest.approx(4.63, abs=0.01)
        # CS/min : 6.667, 6, 6, 6, 6 → 6.13
        assert s.avg_cs_per_min == pytest.approx(6.13, abs=0.01)
        assert s.avg_vision == pytest.approx(20.0)
        assert s.avg_damage == pytest.approx(15000)

    def test_top_champion(self):
        s = compute()
        assert s.top_champion == "Zed"  # 3 parties contre 2 pour Ahri (remake exclu)
        assert s.top_champion_games == 3
        assert s.top_champion_winrate == 100.0

    def test_top_champion_tie_goes_to_most_recent(self):
        player = make_player()
        participants = [
            game(utc(2026, 10, 10, 8, 0), win=True, champion="Ahri"),
            game(utc(2026, 10, 10, 9, 0), win=False, champion="Zed"),
        ]
        s = compute_player_stats(
            player=player, snapshots=[], participants=participants,
            window_start=None, window_end=None, games_limit=10, tz=PARIS, now=NOW,
        )
        assert s.top_champion == "Zed"
        assert s.top_champion_games == 1
        assert s.top_champion_winrate == 0.0

    def test_last_game_at(self):
        s = compute()  # fin de la dernière partie : 22:30 + 20 min
        assert s.last_game_at == "2026-10-10T22:50:00+00:00"

    def test_no_data(self):
        player = make_player(puuid=None, game_name=None, tag_line=None, team_id=None)
        s = compute_player_stats(
            player=player, snapshots=[], participants=[],
            window_start=WINDOW_START, window_end=None, games_limit=10, tz=PARIS, now=NOW,
        )
        assert s.is_linked is False
        assert s.riot_id is None
        assert s.tier is None
        assert s.rank_label == "Unranked"
        assert s.rank_color == RANK_COLORS["UNRANKED"]
        assert s.absolute_lp is None
        assert s.baseline_absolute_lp is None
        assert s.lp_net == 0
        assert s.games == 0
        assert s.winrate is None
        assert s.games_today == 0
        assert s.games_per_day == {}
        assert s.streak == "—"
        assert s.avg_kda is None
        assert s.avg_cs_per_min is None
        assert s.top_champion is None
        assert s.top_champion_games == 0
        assert s.last_game_at is None
        assert s.live is None

    def test_unranked_snapshot_gives_zero_lp_net(self):
        player = make_player()
        snapshots = [
            snap(utc(2026, 10, 9, 23, 0), None, None, 0),
            snap(utc(2026, 10, 10, 10, 0), "GOLD", "IV", 10),
        ]
        s = compute_player_stats(
            player=player, snapshots=snapshots, participants=[],
            window_start=WINDOW_START, window_end=None, games_limit=10, tz=PARIS, now=NOW,
        )
        assert s.baseline_absolute_lp is None
        assert s.absolute_lp == 1210
        assert s.lp_net == 0

    def test_flex_queue(self):
        s = compute(queue=Queue.FLEX)
        assert (s.tier, s.rank, s.lp) == ("PLATINUM", "IV", 0)
        assert s.games == 1
        assert s.top_champion == "Jinx"

    def test_naive_datetimes_from_sqlite(self):
        # SQLite renvoie des datetimes naïfs : ils sont interprétés en UTC
        player, snapshots, participants = scenario()
        for snapshot in snapshots:
            snapshot.captured_at = snapshot.captured_at.replace(tzinfo=None)
        for participant in participants:
            participant.game_start = participant.game_start.replace(tzinfo=None)
        s = compute_player_stats(
            player=player, snapshots=snapshots, participants=participants,
            window_start=WINDOW_START.replace(tzinfo=None), window_end=None,
            games_limit=10, tz=PARIS, now=NOW,
        )
        assert s.lp_net == 60
        assert s.games == 5
        assert s.games_today == 1
        assert s.last_game_at == "2026-10-10T22:50:00+00:00"

    def test_live_game(self):
        live = LiveGameState(
            player_id=1, game_id=42, champion_id=103, champion_name="Ahri",
            queue_id=420, game_mode="CLASSIC",
            game_start=NOW - timedelta(minutes=10), detected_at=NOW - timedelta(minutes=9),
        )
        s = compute(live=live)
        assert s.live is not None
        assert s.live["champion_name"] == "Ahri"
        assert s.live["game_start"] == (NOW - timedelta(minutes=10)).isoformat()
        assert s.live["elapsed_s"] == 600
        assert s.live["queue_id"] == 420
        assert s.live["game_mode"] == "CLASSIC"
        assert "champion_icon_url" in s.live

    def test_live_game_without_known_start_uses_detected_at(self):
        live = LiveGameState(
            player_id=1, game_id=42, champion_id=103, champion_name="Ahri",
            queue_id=420, game_mode="CLASSIC",
            game_start=datetime.fromtimestamp(0, tz=UTC), detected_at=NOW - timedelta(minutes=2),
        )
        s = compute(live=live)
        assert s.live is not None
        assert s.live["elapsed_s"] == 120
        assert s.live["game_start"] == (NOW - timedelta(minutes=2)).isoformat()

    def test_ddragon_urls_when_module_available(self, monkeypatch):
        # Module Data Dragon factice injecté : les URLs d'icônes doivent être câblées dessus
        import sys
        import types

        import app.riot as riot_pkg

        fake = types.ModuleType("app.riot.ddragon")
        fake.CURRENT_VERSION = "15.1.1"  # type: ignore[attr-defined]
        fake.profile_icon_url = lambda version, icon_id: (  # type: ignore[attr-defined]
            None if icon_id is None else f"https://ddragon/{version}/profileicon/{icon_id}.png"
        )
        fake.champion_icon_url = lambda version, name: (  # type: ignore[attr-defined]
            None if not name else f"https://ddragon/{version}/champion/{name}.png"
        )
        monkeypatch.setitem(sys.modules, "app.riot.ddragon", fake)
        monkeypatch.setattr(riot_pkg, "ddragon", fake, raising=False)

        live = LiveGameState(
            player_id=1, game_id=42, champion_id=103, champion_name="Ahri",
            queue_id=420, game_mode="CLASSIC", game_start=NOW, detected_at=NOW,
        )
        s = compute(live=live)
        assert s.icon_url == "https://ddragon/15.1.1/profileicon/5.png"
        assert s.live["champion_icon_url"] == "https://ddragon/15.1.1/champion/Ahri.png"

        # Version explicite prioritaire sur CURRENT_VERSION
        s = compute(live=live, ddragon_version="14.9.1")
        assert s.icon_url == "https://ddragon/14.9.1/profileicon/5.png"
        assert s.live["champion_icon_url"] == "https://ddragon/14.9.1/champion/Ahri.png"

    def test_ddragon_missing_module_falls_back_to_none(self, monkeypatch):
        import builtins
        import sys

        import app.riot as riot_pkg

        real_import = builtins.__import__

        def failing_import(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "app.riot" and fromlist and "ddragon" in fromlist:
                raise ImportError("no ddragon")
            return real_import(name, globals, locals, fromlist, level)

        monkeypatch.delitem(sys.modules, "app.riot.ddragon", raising=False)
        monkeypatch.delattr(riot_pkg, "ddragon", raising=False)
        monkeypatch.setattr(builtins, "__import__", failing_import)

        live = LiveGameState(
            player_id=1, game_id=42, champion_id=103, champion_name="Ahri",
            queue_id=420, game_mode="CLASSIC", game_start=NOW, detected_at=NOW,
        )
        s = compute(live=live)
        assert s.icon_url is None
        assert s.live["champion_icon_url"] is None
        assert s.games == 5  # le reste des stats est intact

    def test_to_dict_is_json_plain(self):
        import json

        live = LiveGameState(
            player_id=1, game_id=42, champion_id=103, champion_name="Ahri",
            queue_id=420, game_mode="CLASSIC", game_start=NOW, detected_at=NOW,
        )
        d = compute(live=live).to_dict()
        assert isinstance(d, dict)
        assert d["display_name"] == "Mike"
        assert d["games_per_day"] == {"2026-10-10": 4, "2026-10-11": 1}
        assert d["live"]["champion_name"] == "Ahri"
        assert d["last_game_at"] == "2026-10-10T22:50:00+00:00"
        json.dumps(d)  # ne lève pas
        # Tous les champs du dataclass sont présents
        assert set(d) == set(PlayerStats.__dataclass_fields__)


# ---------------------------------------------------------------------------
# compute_team_stats / rank_teams / sort_players
# ---------------------------------------------------------------------------


def make_stats(**overrides) -> PlayerStats:
    data = dict(
        player_id=1, display_name="Mike", riot_id="Mike#EUW", is_linked=True, active=True,
        team_id=1, profile_icon_id=None, icon_url=None,
        tier="GOLD", rank="IV", lp=0, rank_label="Gold IV · 0 LP", rank_color=RANK_COLORS["GOLD"],
        absolute_lp=1200, baseline_absolute_lp=1200, lp_net=0,
        games=0, wins=0, losses=0, winrate=None,
        games_today=0, games_per_day={}, games_limit=10,
        streak="—", best_win_streak=0, best_loss_streak=0, hot_streak=False,
        avg_kda=None, avg_cs_per_min=None, avg_vision=None, avg_damage=None,
        top_champion=None, top_champion_games=0, top_champion_winrate=None,
        live=None, last_game_at=None,
    )
    data.update(overrides)
    return PlayerStats(**data)


def make_team_stats(team_id: int, *, lp_net: int, wins: int = 0, losses: int = 0, slot: int | None = None) -> TeamStats:
    games = wins + losses
    return TeamStats(
        team_id=team_id, name=f"Duo {team_id}", color="#000000", slot=slot if slot is not None else team_id,
        position=0, window_start=None, window_end=None,
        lp_net=lp_net, games=games, wins=wins, losses=losses, winrate=winrate(wins, losses),
        games_today=0, live_count=0, players=[],
    )


class TestTeamStats:
    def test_compute_team_stats_aggregates(self):
        team = Team(id=3, name="Duo Vert", color="#22c55e", slot=3, window_start=utc(2026, 10, 10, 8, 0))
        a = make_stats(player_id=1, lp_net=40, games=6, wins=4, losses=2, games_today=3, live={"champion_name": "Ahri"})
        b = make_stats(player_id=2, display_name="Sam", lp_net=-15, games=4, wins=1, losses=3, games_today=2)
        t = compute_team_stats(team, [a, b])
        assert t.team_id == 3
        assert t.name == "Duo Vert"
        assert t.color == "#22c55e"
        assert t.slot == 3
        assert t.position == 0
        assert t.window_start == "2026-10-10T08:00:00+00:00"
        assert t.window_end is None
        assert t.lp_net == 25
        assert t.games == 10
        assert t.wins == 5
        assert t.losses == 5
        assert t.winrate == 50.0
        assert t.games_today == 5
        assert t.live_count == 1
        assert [p.player_id for p in t.players] == [1, 2]

    def test_team_to_dict_nests_players(self):
        team = Team(id=1, name="Duo Rouge", color="#ef4444", slot=1)
        t = compute_team_stats(team, [make_stats()])
        d = t.to_dict()
        assert d["players"][0]["display_name"] == "Mike"
        assert isinstance(d["players"][0], dict)
        assert set(d) == set(TeamStats.__dataclass_fields__)


class TestRankTeams:
    def test_sort_by_lp_net(self):
        teams = [make_team_stats(1, lp_net=10), make_team_stats(2, lp_net=50), make_team_stats(3, lp_net=-20)]
        ranked = rank_teams(teams)
        assert [t.team_id for t in ranked] == [2, 1, 3]
        assert [t.position for t in ranked] == [1, 2, 3]

    def test_tie_on_lp_net_uses_winrate(self):
        teams = [
            make_team_stats(1, lp_net=30, wins=2, losses=2),  # 50 %
            make_team_stats(2, lp_net=30, wins=3, losses=1),  # 75 %
        ]
        assert [t.team_id for t in rank_teams(teams)] == [2, 1]

    def test_tie_on_winrate_uses_games(self):
        teams = [
            make_team_stats(1, lp_net=30, wins=1, losses=1),
            make_team_stats(2, lp_net=30, wins=3, losses=3),
        ]
        assert [t.team_id for t in rank_teams(teams)] == [2, 1]

    def test_winrate_none_ranks_last_on_tie(self):
        teams = [
            make_team_stats(1, lp_net=0),  # aucune partie → winrate None
            make_team_stats(2, lp_net=0, wins=0, losses=2),  # 0 %
        ]
        assert [t.team_id for t in rank_teams(teams)] == [2, 1]

    def test_full_tie_uses_slot(self):
        teams = [make_team_stats(7, lp_net=0, slot=2), make_team_stats(8, lp_net=0, slot=1)]
        ranked = rank_teams(teams)
        assert [t.team_id for t in ranked] == [8, 7]
        assert [t.position for t in ranked] == [1, 2]

    def test_empty(self):
        assert rank_teams([]) == []


class TestSortPlayers:
    def test_default_lp_net_desc(self):
        players = [make_stats(player_id=1, lp_net=5), make_stats(player_id=2, lp_net=40), make_stats(player_id=3, lp_net=-3)]
        assert [p.player_id for p in sort_players(players)] == [2, 1, 3]

    def test_winrate_none_last(self):
        players = [
            make_stats(player_id=1, winrate=None),
            make_stats(player_id=2, winrate=40.0),
            make_stats(player_id=3, winrate=80.0),
        ]
        assert [p.player_id for p in sort_players(players, "winrate")] == [3, 2, 1]

    def test_games(self):
        players = [make_stats(player_id=1, games=2), make_stats(player_id=2, games=9)]
        assert [p.player_id for p in sort_players(players, "games")] == [2, 1]

    def test_kda_maps_to_avg_kda(self):
        players = [
            make_stats(player_id=1, avg_kda=2.5),
            make_stats(player_id=2, avg_kda=None),
            make_stats(player_id=3, avg_kda=6.0),
        ]
        assert [p.player_id for p in sort_players(players, "kda")] == [3, 1, 2]

    def test_unknown_key_falls_back_to_lp_net(self):
        players = [make_stats(player_id=1, lp_net=5), make_stats(player_id=2, lp_net=40)]
        assert [p.player_id for p in sort_players(players, "nope")] == [2, 1]

    def test_does_not_mutate_input(self):
        players = [make_stats(player_id=1, lp_net=5), make_stats(player_id=2, lp_net=40)]
        sort_players(players)
        assert [p.player_id for p in players] == [1, 2]


def test_module_exports_contract_names():
    for name in [
        "TIERS", "DIVISIONS", "RANK_COLORS", "absolute_lp", "rank_from_absolute_lp", "format_rank",
        "rank_color", "StreakInfo", "compute_streak", "kda", "winrate", "day_key", "PlayerStats",
        "compute_player_stats", "TeamStats", "compute_team_stats", "rank_teams", "sort_players",
    ]:
        assert hasattr(stats, name), name
