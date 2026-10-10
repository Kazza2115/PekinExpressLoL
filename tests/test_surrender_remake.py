"""Abandons (« /ff ») vus du bon côté, et remakes exclus des parties du jour.

- Riot marque `gameEndedInSurrender` pour les 10 joueurs : seule l'équipe perdante a abandonné.
  Une victoire par abandon adverse n'est donc pas un abandon pour le joueur.
- Un remake (`gameEndedInEarlySurrender`, ou partie de moins de 5 min) ne compte ni dans les
  stats ni dans les parties du jour, même voté après 5 min.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, select

from app.api.leaderboard import build_leaderboard, together_surrenders
from app.db.models import (
    REMAKE_FLAG_MAX_DURATION_S,
    Challenge,
    ChallengeStatus,
    Match,
    MatchParticipant,
    Player,
    Queue,
    Team,
    is_remake_game,
)
from app.events import bus
from app.services.bootstrap import reclassify_remakes
from app.services.notifications import MatchNotice, match_embed
from app.services.poller import Poller
from app.services.scoreboard import build_scoreboard
from app.services.stats import compute_player_stats, compute_team_stats
from app.state import state
from tests.test_discord_embeds import make_settings, mike
from tests.test_discord_embeds import part as discord_part
from tests.test_poller import ScriptedAPI, gold_iv, make_challenge, make_player, match_json
from tests.test_profile_stats import NOW, PARIS, WINDOW_START, _member, game, scenario, utc

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Remakes
# --------------------------------------------------------------------------- #


def test_is_remake_game_uses_riot_flag_and_duration():
    flagged = [{"gameEndedInEarlySurrender": True}, {"gameEndedInEarlySurrender": False}]
    assert is_remake_game(200) is True  # très courte : remake même sans drapeau
    assert is_remake_game(420, flagged) is True  # remake voté tard (7 min)
    assert is_remake_game(420, [{"gameEndedInEarlySurrender": False}]) is False
    assert is_remake_game(420, [{"gameEndedInSurrender": True}]) is False  # abandon normal
    # À partir de 15 min, une vraie partie peut finir par abandon : le drapeau n'est plus cru
    assert is_remake_game(REMAKE_FLAG_MAX_DURATION_S, flagged) is False
    assert is_remake_game(1800, flagged) is False


async def test_late_remake_is_not_a_game_of_the_day(session: Session):
    """Un remake voté à 6 min (drapeau Riot) n'est ni compté, ni numéroté dans la journée."""
    make_challenge(session, ChallengeStatus.RUNNING, start_at=utcnow() - timedelta(hours=3))
    make_player(session, "Mike", "p-mike")
    api = ScriptedAPI()
    api.entries["p-mike"] = gold_iv(50)
    api.match_ids["p-mike"] = ["EUW1_2", "EUW1_1"]
    remake = match_json("EUW1_1", [("p-mike", "Ahri", False)], utcnow() - timedelta(minutes=90), 360)
    for participant in remake["info"]["participants"]:
        participant["gameEndedInEarlySurrender"] = True
    api.matches["EUW1_1"] = remake
    api.matches["EUW1_2"] = match_json("EUW1_2", [("p-mike", "Ahri", True)], utcnow() - timedelta(minutes=50), 1700)

    report = await Poller(api, bus, state).poll_once()
    assert report.errors == [] and report.new_matches == 2
    session.expire_all()
    rows = {row.match_id: row for row in session.exec(select(MatchParticipant)).all()}
    assert rows["EUW1_1"].is_remake is True and rows["EUW1_2"].is_remake is False

    _teams, players = build_leaderboard(session, session.exec(select(Challenge)).one())
    stats = players[0]
    assert (stats.games, stats.wins, stats.losses) == (1, 1, 0)
    assert sum(stats.games_per_day.values()) == 1  # le remake n'occupe pas une place du quota


def _store_match(session: Session, match_id: str, duration: int, *, flagged: bool, remake: bool) -> None:
    start = utc(2026, 10, 10, 10, 0)
    raw = {
        "info": {
            "gameDuration": duration,
            "participants": [{"puuid": "p-mike", "gameEndedInEarlySurrender": flagged}, {"puuid": "x"}],
        }
    }
    session.add(Match(match_id=match_id, queue_id=420, game_start=start, game_duration=duration, raw_json=json.dumps(raw)))
    session.add(
        MatchParticipant(
            match_id=match_id, player_id=1, queue=Queue.SOLO, game_start=start, game_duration=duration,
            is_remake=remake, champion_name="Ahri", win=False,
        )
    )
    session.commit()


def test_reclassify_remakes_fixes_games_stored_with_the_old_rule(session: Session):
    session.add(Player(id=1, display_name="Mike", game_name="Mike", tag_line="EUW", puuid="p-mike"))
    session.commit()
    _store_match(session, "EUW1_LATE", 400, flagged=True, remake=False)  # remake voté tard, compté à tort
    _store_match(session, "EUW1_REAL", 400, flagged=False, remake=False)  # vraie partie courte (abandon…)
    _store_match(session, "EUW1_LONG", 1900, flagged=True, remake=False)  # drapeau ignoré après 15 min
    assert reclassify_remakes(session) == 1
    session.expire_all()
    rows = {row.match_id: row.is_remake for row in session.exec(select(MatchParticipant)).all()}
    assert rows == {"EUW1_LATE": True, "EUW1_REAL": False, "EUW1_LONG": False}
    assert reclassify_remakes(session) == 0  # idempotent


def test_scoreboard_flags_a_late_remake():
    raw = {
        "info": {
            "gameDuration": 400,
            "gameEndTimestamp": 1,
            "participants": [
                {"puuid": f"p{i}", "teamId": 100 if i < 5 else 200, "win": i >= 5, "gameEndedInEarlySurrender": True}
                for i in range(10)
            ],
        }
    }
    board = build_scoreboard(Match(match_id="EUW1_9", queue_id=420, game_start=utcnow(), game_duration=400,
                                   raw_json=json.dumps(raw)), {})
    assert board["remake"] is True and board["surrender"] is False
    assert not any(team["surrendered"] for team in board["teams"])


# --------------------------------------------------------------------------- #
# Abandons
# --------------------------------------------------------------------------- #


def test_win_by_enemy_surrender_is_not_a_surrender_for_the_player():
    player, snapshots, participants = scenario()
    participants += [
        game(utc(2026, 10, 10, 15, 0), 1500, win=True, surrendered=True),  # l'adversaire a abandonné
        game(utc(2026, 10, 10, 16, 0), 1600, win=True, surrendered=True),
        game(utc(2026, 10, 10, 17, 0), 200, win=False, surrendered=True, is_remake=True),  # remake : ignoré
    ]
    stats = compute_player_stats(
        player=player, snapshots=snapshots, participants=participants, window_start=WINDOW_START,
        window_end=None, games_limit=10, tz=PARIS, now=NOW,
    )
    # Une seule défaite par abandon dans le scénario (14:00), deux victoires par abandon adverse
    assert (stats.surrenders, stats.surrender_wins) == (1, 2)


def test_duo_surrender_together_counts_once():
    team = Team(id=1, name="Duo Rouge", color="#e5484d", slot=1)
    a = _member(1, surrenders=2, surrender_wins=1)
    b = _member(2, surrenders=1, surrender_wins=1)
    stats = compute_team_stats(team, [a, b], together=(2, 1, 1), together_surrenders=(1, 1))
    assert (stats.surrenders, stats.surrender_wins) == (2, 1)
    assert compute_team_stats(team, [a, b]).surrenders == 3  # sans partie commune : somme


def test_together_surrenders_splits_by_result():
    def row(match_id: str, player_id: int, *, win: bool, surrendered: bool) -> MatchParticipant:
        return MatchParticipant(
            match_id=match_id, player_id=player_id, queue=Queue.SOLO, game_start=utc(2026, 10, 10, 12, 0),
            game_duration=1600, champion_name="Ahri", team_side=100, win=win, surrendered=surrendered,
        )

    a = [row("M1", 1, win=False, surrendered=True), row("M2", 1, win=True, surrendered=True),
         row("M3", 1, win=True, surrendered=False)]
    b = [row("M1", 2, win=False, surrendered=True), row("M2", 2, win=True, surrendered=True),
         row("M3", 2, win=True, surrendered=False)]
    assert together_surrenders(a, b, None, None) == (1, 1)
    assert together_surrenders(a, b, None, None, exclude_match_ids={"M1"}) == (0, 1)


def test_scoreboard_marks_the_team_that_surrendered():
    raw = {
        "info": {
            "gameDuration": 1500,
            "gameEndTimestamp": 1,
            "participants": [
                {"puuid": f"p{i}", "teamId": 100 if i < 5 else 200, "win": i < 5, "gameEndedInSurrender": True}
                for i in range(10)
            ],
        }
    }
    board = build_scoreboard(Match(match_id="EUW1_7", queue_id=420, game_start=utcnow(), game_duration=1500,
                                   raw_json=json.dumps(raw)), {})
    assert board["surrender"] is True and board["remake"] is False
    assert {team["side"]: team["surrendered"] for team in board["teams"]} == {"blue": False, "red": True}


def test_discord_card_says_who_surrendered():
    win = discord_part(1, win=True)
    win.surrendered = True
    loss = discord_part(1, win=False)
    loss.surrendered = True
    plain = discord_part(1, win=True)
    settings = make_settings()
    assert "Victoire par abandon adverse" in match_embed(MatchNotice(player=mike(), team=None, participant=win),
                                                         settings=settings)["description"]
    assert "Défaite par abandon" in match_embed(MatchNotice(player=mike(), team=None, participant=loss),
                                                settings=settings)["description"]
    assert "abandon" not in match_embed(MatchNotice(player=mike(), team=None, participant=plain),
                                        settings=settings)["description"]
