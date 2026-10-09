"""Quota quotidien : au-delà de `games_per_day` parties terminées dans la journée (heure de
Paris), une partie ne compte plus (ni LP, ni stats)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.leaderboard import together_record
from app.db.models import ChallengeStatus, Match, MatchParticipant, Queue
from app.services.notifications import format_match_recorded
from app.services.poller import Poller
from app.services.stats import compute_player_stats, over_quota_lp, split_daily_quota
from app.state import state
from tests.test_poller import ScriptedAPI, events_of, make_challenge, make_player, match_json
from tests.test_stats import PARIS, game, make_player as stats_player, snap, utc

DAY = utc(2026, 10, 10, 0, 0)


def twelve_games_and_snapshots():
    """12 parties le 10 octobre (6h–17h UTC) puis 1 le 11 à 00h30 heure de Paris.

    Master : LP absolus = 2800 + LP. Un relevé 2 min après chaque partie. Les 10 premières :
    +20 ; la 11e : +25 ; la 12e : −15 ; celle du 11 : +20.
    """
    games, snapshots = [], [snap(utc(2026, 10, 9, 12, 0), "MASTER", None, 100)]
    lp = 100
    deltas = [20] * 10 + [25, -15]
    for i, delta in enumerate(deltas):
        start = DAY + timedelta(hours=6 + i)
        games.append(game(start, win=delta > 0, duration=1200))
        lp += delta
        snapshots.append(snap(start + timedelta(minutes=22), "MASTER", None, lp))
    next_day = game(utc(2026, 10, 10, 22, 10), win=True, duration=1200)  # fin 22:30 UTC = 00:30 le 11 à Paris
    games.append(next_day)
    lp += 20
    snapshots.append(snap(utc(2026, 10, 10, 22, 32), "MASTER", None, lp))
    return games, snapshots


def test_split_daily_quota_by_paris_day() -> None:
    games, _ = twelve_games_and_snapshots()
    counted, over = split_daily_quota(list(reversed(games)), PARIS, 10)
    assert len(counted) == 11 and len(over) == 2
    assert over == games[10:12]  # 11e et 12e du 10 octobre
    assert games[12] in counted  # 00:30 à Paris : nouvelle journée
    assert split_daily_quota(games, PARIS, 0) == (games, [])  # pas de quota


def test_over_quota_lp_and_prorata() -> None:
    games, snapshots = twelve_games_and_snapshots()
    assert over_quota_lp(snapshots, games, games[10:12]) == (10, False)  # +25 − 15
    assert over_quota_lp(snapshots, games, []) == (0, False)

    # Serveur arrêté : un seul relevé couvre la 10e (comptée) et la 11e (hors quota) → prorata
    merged = [s for i, s in enumerate(snapshots) if i != 10]  # relevé d'après la 10e retiré
    lp, approx = over_quota_lp(merged, games, games[10:12])
    assert approx is True
    assert lp == round((20 + 25) / 2) - 15


def test_player_stats_ignore_games_beyond_quota() -> None:
    games, snapshots = twelve_games_and_snapshots()
    stats = compute_player_stats(
        player=stats_player(),
        snapshots=snapshots,
        participants=games,
        window_start=DAY,
        window_end=None,
        games_limit=10,
        tz=PARIS,
        now=utc(2026, 10, 10, 23, 0),
    )
    assert stats.lp_net_all_games == 10 * 20 + 25 - 15 + 20
    assert stats.lp_over_quota == 10
    assert stats.lp_net == stats.lp_net_all_games - 10 == 220
    assert stats.games == 11 and stats.wins == 11 and stats.losses == 0  # la défaite hors quota ne compte pas
    assert stats.games_over_quota == 2
    assert stats.over_quota_match_ids == [games[10].match_id, games[11].match_id]
    assert stats.games_per_day == {"2026-10-10": 10, "2026-10-11": 1}
    assert stats.games_today == 1 and stats.games_today_over_quota == 0
    by_day = {d["day"]: d for d in stats.by_day}
    assert by_day["2026-10-10"]["games"] == 10 and by_day["2026-10-10"]["over_quota"] == 2
    assert by_day["2026-10-11"]["over_quota"] == 0
    assert stats.lp_per_game == round(220 / 11, 1)
    assert stats.lp_over_quota_approx is False


def test_together_record_skips_over_quota_games() -> None:
    start = utc(2026, 10, 10, 8, 0)
    a = [game(start, win=True, match_id="M1", team_side=100), game(start + timedelta(hours=1), win=False, match_id="M2", team_side=100)]
    b = [game(start, win=True, match_id="M1", team_side=100, player_id=2), game(start + timedelta(hours=1), win=False, match_id="M2", team_side=100, player_id=2)]
    assert together_record(a, b, None, None) == (2, 1, 1)
    assert together_record(a, b, None, None, exclude_match_ids={"M2"}) == (1, 1, 0)


def test_discord_text_marks_over_quota() -> None:
    games, _ = twelve_games_and_snapshots()
    participant = games[10]
    participant.kills, participant.deaths, participant.assists = 7, 2, 9
    text = format_match_recorded(stats_player(), None, participant, 25, over_quota=True, day_number=11)
    assert text.endswith("⛔ hors quota (11e partie du jour, ne compte pas)")
    assert "hors quota" not in format_match_recorded(stats_player(), None, participant, 25)


@pytest.mark.anyio
async def test_poller_flags_eleventh_game_of_the_day(session: Session) -> None:
    make_challenge(session, ChallengeStatus.RUNNING, start_at=DAY)
    make_player(session, "Mike", "p-mike")
    api = ScriptedAPI()
    ids = []
    for i in range(11):
        match_id = f"EUW1_{900 + i}"
        api.matches[match_id] = match_json(match_id, [("p-mike", "Ahri", True)], DAY + timedelta(hours=6 + i), 1200)
        ids.append(match_id)
    api.match_ids["p-mike"] = list(reversed(ids))  # Riot : du plus récent au plus ancien
    from app.events import bus

    await Poller(api, bus, state).poll_once()
    recorded = {e["data"]["match_id"]: e["data"] for e in events_of("match_recorded")}
    assert recorded["EUW1_909"]["over_quota"] is False and recorded["EUW1_909"]["day_game_number"] == 10
    assert recorded["EUW1_910"]["over_quota"] is True and recorded["EUW1_910"]["day_game_number"] == 11


def test_api_marks_over_quota_games(client: TestClient, session: Session) -> None:
    make_challenge(session, ChallengeStatus.RUNNING, start_at=DAY)
    player = make_player(session, "Mike", "p-mike")
    for i in range(11):
        start = DAY + timedelta(hours=6 + i)
        match_id = f"EUW1_{700 + i}"
        session.add(Match(match_id=match_id, queue_id=420, game_start=start, game_end=start + timedelta(minutes=20), game_duration=1200, raw_json="{}"))
        session.add(
            MatchParticipant(
                match_id=match_id, player_id=player.id, queue=Queue.SOLO, game_start=start,
                game_end=start + timedelta(minutes=20), game_duration=1200, champion_name="Ahri", win=True,
            )
        )
    session.commit()

    body = client.get(f"/api/players/{player.id}").json()
    assert body["stats"]["games"] == 10 and body["stats"]["games_over_quota"] == 1
    flags = {m["match_id"]: m["over_quota"] for m in body["matches"]}
    assert flags["EUW1_710"] is True and sum(flags.values()) == 1

    feed = client.get("/api/feed").json()["items"]
    assert [m["match_id"] for m in feed if m["over_quota"]] == ["EUW1_710"]
