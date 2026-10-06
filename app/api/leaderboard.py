"""Construction du classement : chargement des données puis calculs purs (`services.stats`).

La fenêtre d'un joueur est celle de son duo (`Team.window_*`) sinon celle du challenge.
Tant que le challenge n'a pas démarré, la fenêtre est ouverte (`None`) : les pages
montrent alors les stats sur tout l'historique connu.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlmodel import Session, col, select

from app.config import get_settings
from app.db.models import Challenge, ChallengeStatus, MatchParticipant, Player, RankSnapshot, Team, utcnow
from app.db.session import as_utc
from app.riot import ddragon
from app.services.stats import (
    PlayerStats,
    TeamStats,
    compute_player_stats,
    compute_team_stats,
    rank_teams,
    sort_players,
)
from app.state import state

SORT_KEYS = ("lp_net", "winrate", "games", "kda")
STARTED_STATUSES = (ChallengeStatus.RUNNING, ChallengeStatus.FINISHED)


def challenge_window(challenge: Challenge) -> tuple[datetime | None, datetime | None]:
    """Fenêtre du challenge (UTC) ; (None, None) tant qu'il n'a pas démarré."""
    if challenge.status not in STARTED_STATUSES:
        return None, None
    return as_utc(challenge.start_at), as_utc(challenge.end_at)


def team_window(challenge: Challenge, team: Team | None) -> tuple[datetime | None, datetime | None]:
    """Fenêtre d'un duo : ses propres bornes si définies, sinon celles du challenge."""
    start, end = challenge_window(challenge)
    if team is not None:
        start = as_utc(team.window_start) or start
        end = as_utc(team.window_end) or end
    return start, end


def load_history(
    session: Session, player_ids: list[int]
) -> tuple[dict[int, list[RankSnapshot]], dict[int, list[MatchParticipant]]]:
    """Snapshots et participations des joueurs, triés par date croissante, groupés par joueur."""
    snapshots_by: dict[int, list[RankSnapshot]] = defaultdict(list)
    participants_by: dict[int, list[MatchParticipant]] = defaultdict(list)
    if not player_ids:
        return snapshots_by, participants_by
    snapshots = session.exec(
        select(RankSnapshot)
        .where(col(RankSnapshot.player_id).in_(player_ids))
        .order_by(col(RankSnapshot.captured_at), col(RankSnapshot.id))
    ).all()
    for snapshot in snapshots:
        snapshots_by[snapshot.player_id].append(snapshot)
    participants = session.exec(
        select(MatchParticipant)
        .where(col(MatchParticipant.player_id).in_(player_ids))
        .order_by(col(MatchParticipant.game_start), col(MatchParticipant.id))
    ).all()
    for participant in participants:
        participants_by[participant.player_id].append(participant)
    return snapshots_by, participants_by


def _player_stats(
    player: Player,
    challenge: Challenge,
    team: Team | None,
    snapshots: list[RankSnapshot],
    participants: list[MatchParticipant],
    now: datetime,
) -> PlayerStats:
    window_start, window_end = team_window(challenge, team)
    return compute_player_stats(
        player=player,
        snapshots=snapshots,
        participants=participants,
        window_start=window_start,
        window_end=window_end,
        games_limit=challenge.games_per_day,
        tz=get_settings().tz,
        now=now,
        live=state.live_games.get(player.id),
        ddragon_version=ddragon.CURRENT_VERSION,
    )


def compute_single_player_stats(
    session: Session,
    challenge: Challenge,
    player: Player,
    team: Team | None,
    *,
    now: datetime | None = None,
) -> PlayerStats:
    """Stats d'un seul joueur (fiche)."""
    now = now or utcnow()
    snapshots_by, participants_by = load_history(session, [player.id])
    return _player_stats(
        player, challenge, team, snapshots_by.get(player.id, []), participants_by.get(player.id, []), now
    )


def build_leaderboard(
    session: Session,
    challenge: Challenge,
    *,
    now: datetime | None = None,
    sort: str = "lp_net",
) -> tuple[list[TeamStats], list[PlayerStats]]:
    """Classement des duos (triés via `rank_teams`) et des joueurs actifs (triés via `sort_players`)."""
    now = now or utcnow()
    players = session.exec(select(Player).where(col(Player.active).is_(True)).order_by(col(Player.id))).all()
    teams = session.exec(select(Team).order_by(col(Team.slot), col(Team.id))).all()
    teams_by_id = {team.id: team for team in teams}
    snapshots_by, participants_by = load_history(session, [p.id for p in players if p.id is not None])

    stats_by_player: dict[int, PlayerStats] = {}
    for player in players:
        assert player.id is not None
        team = teams_by_id.get(player.team_id) if player.team_id is not None else None
        stats_by_player[player.id] = _player_stats(
            player, challenge, team, snapshots_by.get(player.id, []), participants_by.get(player.id, []), now
        )

    team_stats: list[TeamStats] = []
    for team in teams:
        members = [stats_by_player[p.id] for p in players if p.team_id == team.id and p.id in stats_by_player]
        team_stats.append(compute_team_stats(team, members))
    ranked_teams = rank_teams(team_stats)
    ranked_players = sort_players(list(stats_by_player.values()), key=sort)
    return ranked_teams, ranked_players
