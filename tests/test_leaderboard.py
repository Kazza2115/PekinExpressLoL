"""`app.api.leaderboard` : parties jouées ensemble (`together_record`) et câblage dans le classement."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session

from app.api.leaderboard import build_leaderboard, together_record
from app.db.models import Challenge, ChallengeStatus, Match, MatchParticipant, Player, Queue, Team


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


def part(
    match_id: str,
    player_id: int,
    *,
    side: int | None,
    win: bool = True,
    start: datetime = utc(2026, 10, 10, 12, 0),
    duration: int = 1800,
    remake: bool = False,
    queue: Queue = Queue.SOLO,
    game_end: datetime | None = None,
) -> MatchParticipant:
    return MatchParticipant(
        match_id=match_id,
        player_id=player_id,
        queue=queue,
        game_start=start,
        game_end=game_end,
        game_duration=duration,
        is_remake=remake,
        champion_name="Ahri",
        team_side=side,
        win=win,
    )


class TestTogetherRecord:
    def test_same_match_same_side_counts(self):
        a = [part("M1", 1, side=100, win=True), part("M2", 1, side=200, win=False), part("M3", 1, side=100)]
        b = [part("M1", 2, side=100, win=True), part("M2", 2, side=200, win=False), part("M9", 2, side=100)]
        assert together_record(a, b, None, None) == (2, 1, 1)

    def test_opposite_sides_do_not_count(self):
        a = [part("M1", 1, side=100, win=True)]
        b = [part("M1", 2, side=200, win=False)]
        assert together_record(a, b, None, None) == (0, 0, 0)

    def test_unknown_side_does_not_count(self):
        a = [part("M1", 1, side=None, win=True)]
        b = [part("M1", 2, side=None, win=True)]
        assert together_record(a, b, None, None) == (0, 0, 0)
        assert together_record([part("M1", 1, side=100)], [part("M1", 2, side=None)], None, None) == (0, 0, 0)

    def test_remakes_are_excluded(self):
        a = [part("M1", 1, side=100, remake=True, duration=180)]
        b = [part("M1", 2, side=100, remake=True, duration=180)]
        assert together_record(a, b, None, None) == (0, 0, 0)

    def test_window_uses_game_end(self):
        start = utc(2026, 10, 10, 12, 0)
        # Commence avant la fenêtre mais se termine dedans → compte
        a = [part("M1", 1, side=100, start=start - timedelta(minutes=20), duration=1800)]
        b = [part("M1", 2, side=100, start=start - timedelta(minutes=20), duration=1800)]
        assert together_record(a, b, start, None) == (1, 1, 0)
        # Se termine avant → exclue
        a = [part("M1", 1, side=100, start=start - timedelta(hours=2), duration=1800)]
        b = [part("M1", 2, side=100, start=start - timedelta(hours=2), duration=1800)]
        assert together_record(a, b, start, None) == (0, 0, 0)
        # Se termine après la fin de fenêtre → exclue ; `game_end` explicite prioritaire
        end = start + timedelta(hours=1)
        a = [part("M1", 1, side=100, start=start, duration=600, game_end=end + timedelta(minutes=1))]
        b = [part("M1", 2, side=100, start=start, duration=600, game_end=end + timedelta(minutes=1))]
        assert together_record(a, b, start, end) == (0, 0, 0)
        # Datetimes naïfs (SQLite) acceptés
        a = [part("M1", 1, side=100, start=start.replace(tzinfo=None))]
        b = [part("M1", 2, side=100, start=start.replace(tzinfo=None))]
        assert together_record(a, b, start, end) == (1, 1, 0)

    def test_queue_filter_defaults_to_solo(self):
        a = [part("F1", 1, side=100, queue=Queue.FLEX), part("S1", 1, side=100)]
        b = [part("F1", 2, side=100, queue=Queue.FLEX), part("S1", 2, side=100)]
        assert together_record(a, b, None, None) == (1, 1, 0)
        assert together_record(a, b, None, None, queue=None) == (2, 2, 0)
        assert together_record(a, b, None, None, queue=Queue.FLEX) == (1, 1, 0)

    def test_duplicate_rows_count_once(self):
        a = [part("M1", 1, side=100)]
        b = [part("M1", 2, side=100), part("M1", 2, side=100)]
        assert together_record(a, b, None, None) == (1, 1, 0)

    def test_empty(self):
        assert together_record([], [], None, None) == (0, 0, 0)
        assert together_record([part("M1", 1, side=100)], [], None, None) == (0, 0, 0)


def _seed(session: Session) -> tuple[Challenge, Team, Player, Player, Player]:
    start_at = datetime.now(timezone.utc) - timedelta(hours=2)
    challenge = Challenge(status=ChallengeStatus.RUNNING, start_at=start_at)
    team = Team(name="Duo Rouge", color="#ef4444", slot=1)
    session.add(challenge)
    session.add(team)
    session.commit()
    mike = Player(display_name="Mike", game_name="Mike", tag_line="EUW", puuid="p-mike", team_id=team.id)
    lea = Player(display_name="Léa", game_name="Lea", tag_line="EUW", puuid="p-lea", team_id=team.id)
    sam = Player(display_name="Sam", game_name="Sam", tag_line="EUW", puuid="p-sam")
    session.add_all([mike, lea, sam])
    session.commit()
    for player in (mike, lea, sam):
        session.refresh(player)
    return challenge, team, mike, lea, sam


def _match(session: Session, match_id: str, start: datetime, rows: list[tuple[int, int | None, bool]]) -> None:
    session.add(Match(match_id=match_id, queue_id=420, game_start=start, game_duration=1800, raw_json="{}"))
    for player_id, side, win in rows:
        session.add(part(match_id, player_id, side=side, win=win, start=start))
    session.commit()


def test_build_leaderboard_fills_together_record(session: Session):
    challenge, team, mike, lea, sam = _seed(session)
    now = datetime.now(timezone.utc)
    # Ensemble (même côté) : 1 victoire ; l'un contre l'autre : ne compte pas ; avant la fenêtre : exclue
    _match(session, "M1", now - timedelta(hours=1), [(mike.id, 100, True), (lea.id, 100, True)])
    _match(session, "M2", now - timedelta(minutes=50), [(mike.id, 100, False), (lea.id, 200, True)])
    _match(session, "M3", now - timedelta(hours=5), [(mike.id, 200, True), (lea.id, 200, True)])
    # Mike avec Sam (pas son duo) : sans effet sur le duo
    _match(session, "M4", now - timedelta(minutes=30), [(mike.id, 100, True), (sam.id, 100, True)])

    teams, players = build_leaderboard(session, challenge, now=now)
    assert len(teams) == 1
    duo = teams[0]
    assert (duo.together_games, duo.together_wins, duo.together_losses, duo.together_winrate) == (1, 1, 0, 100.0)
    assert duo.games == 5  # Mike 3 (M1, M2, M4) + Léa 2 (M1, M2) dans la fenêtre
    assert duo.mvp_player_id in (mike.id, lea.id)
    assert duo.avg_kda is not None
    assert {p.player_id for p in players} == {mike.id, lea.id, sam.id}


def test_build_leaderboard_incomplete_team_has_no_together(session: Session):
    challenge, team, mike, lea, sam = _seed(session)
    lea.team_id = None
    session.add(lea)
    session.commit()
    now = datetime.now(timezone.utc)
    _match(session, "M1", now - timedelta(hours=1), [(mike.id, 100, True), (lea.id, 100, True)])
    teams, _ = build_leaderboard(session, challenge, now=now)
    assert teams[0].together_games == 0
    assert [p.player_id for p in teams[0].players] == [mike.id]
    assert teams[0].mvp_player_id == mike.id
