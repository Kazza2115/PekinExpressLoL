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
from app.db.models import (
    Challenge,
    ChallengeStatus,
    MatchParticipant,
    Player,
    Queue,
    RankSnapshot,
    Team,
    game_end_of,
    utcnow,
)
from app.db.session import as_utc
from app.riot import ddragon
from app.services.stats import (
    PlayerStats,
    TeamStats,
    compute_player_stats,
    compute_team_stats,
    partner_record,
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


def together_record(
    participants_a: list[MatchParticipant],
    participants_b: list[MatchParticipant],
    window_start: datetime | None,
    window_end: datetime | None,
    *,
    queue: Queue | None = Queue.SOLO,
    exclude_match_ids: set[str] | None = None,
) -> tuple[int, int, int]:
    """(parties, victoires, défaites) jouées *ensemble* par deux joueurs.

    Une partie compte si les deux y étaient, du même côté (`team_side` égal et connu),
    hors remake, et si elle se termine dans la fenêtre (`game_end_of`). Comme les stats
    joueur, seule la file `queue` est prise en compte (None → toutes les files).
    `exclude_match_ids` : parties hors quota quotidien de l'un ou l'autre (non comptées).
    """
    excluded = exclude_match_ids or set()
    start_utc = as_utc(window_start)
    end_utc = as_utc(window_end)

    def eligible(p: MatchParticipant) -> bool:
        if p.is_remake or p.team_side is None:
            return False
        return queue is None or p.queue == queue

    sides_a = {p.match_id: p for p in participants_a if eligible(p)}
    games = wins = losses = 0
    seen: set[str] = set()
    for part_b in participants_b:
        if not eligible(part_b) or part_b.match_id in seen or part_b.match_id in excluded:
            continue
        part_a = sides_a.get(part_b.match_id)
        if part_a is None or part_a.team_side != part_b.team_side:
            continue
        ended_at = game_end_of(part_b)
        if start_utc is not None and ended_at < start_utc:
            continue
        if end_utc is not None and ended_at > end_utc:
            continue
        seen.add(part_b.match_id)
        games += 1
        if part_a.win:
            wins += 1
        else:
            losses += 1
    return games, wins, losses


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
    """Stats d'un seul joueur (fiche), avec son coéquipier (`partner`) s'il a un duo complet."""
    now = now or utcnow()
    mates: list[Player] = []
    if team is not None and team.id is not None:
        mates = list(
            session.exec(
                select(Player)
                .where(col(Player.team_id) == team.id, col(Player.id) != player.id, col(Player.active).is_(True))
                .order_by(col(Player.id))
            ).all()
        )
    partner = mates[0] if len(mates) == 1 and player.active else None
    ids = [player.id] + ([partner.id] if partner is not None else [])
    snapshots_by, participants_by = load_history(session, [pid for pid in ids if pid is not None])
    stats = _player_stats(
        player, challenge, team, snapshots_by.get(player.id, []), participants_by.get(player.id, []), now
    )
    if partner is not None and partner.id is not None:
        window_start, window_end = team_window(challenge, team)
        partner_stats = _player_stats(
            partner, challenge, team, snapshots_by.get(partner.id, []), participants_by.get(partner.id, []), now
        )
        stats.partner = partner_record(
            stats,
            partner_id=partner.id,
            display_name=partner.display_name,
            icon_url=ddragon.profile_icon_url(ddragon.CURRENT_VERSION, partner.profile_icon_id),
            together=together_record(
                participants_by.get(player.id, []),
                participants_by.get(partner.id, []),
                window_start,
                window_end,
                exclude_match_ids=set(stats.over_quota_match_ids) | set(partner_stats.over_quota_match_ids),
            ),
        )
    return stats


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
        together = (0, 0, 0)
        if len(members) == 2:
            window_start, window_end = team_window(challenge, team)
            together = together_record(
                participants_by.get(members[0].player_id, []),
                participants_by.get(members[1].player_id, []),
                window_start,
                window_end,
                exclude_match_ids=set(members[0].over_quota_match_ids) | set(members[1].over_quota_match_ids),
            )
            for me, mate in ((members[0], members[1]), (members[1], members[0])):
                me.partner = partner_record(
                    me,
                    partner_id=mate.player_id,
                    display_name=mate.display_name,
                    icon_url=mate.icon_url,
                    together=together,
                )
        team_stats.append(compute_team_stats(team, members, together=together))
    ranked_teams = rank_teams(team_stats)
    ranked_players = sort_players(list(stats_by_player.values()), key=sort)
    return ranked_teams, ranked_players
