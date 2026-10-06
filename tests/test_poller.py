"""Tests du poller : client démo déterministe, API Riot scriptée, calcul des LP par partie."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlmodel import Session, select

from app.config import Settings
from app.db.models import (
    Challenge,
    ChallengeStatus,
    Match,
    MatchParticipant,
    Player,
    Queue,
    RankSnapshot,
    Team,
)
from app.db.session import as_utc
from app.events import bus
from app.riot.base import (
    AccountDTO,
    ActiveGameDTO,
    LeagueEntryDTO,
    RiotError,
    RiotNotFound,
    RiotUnauthorized,
    SummonerDTO,
)
from app.services.poller import Poller, backfill_lp_changes
from app.state import state

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Helpers base de données
# --------------------------------------------------------------------------- #


def make_challenge(session: Session, status: ChallengeStatus, start_at: datetime | None = None) -> Challenge:
    challenge = Challenge(status=status, start_at=start_at)
    session.add(challenge)
    session.commit()
    session.refresh(challenge)
    return challenge


def make_player(
    session: Session,
    display_name: str,
    puuid: str,
    *,
    game_name: str | None = None,
    tag_line: str = "EUW",
    team_id: int | None = None,
) -> Player:
    player = Player(
        display_name=display_name,
        game_name=game_name or display_name,
        tag_line=tag_line,
        puuid=puuid,
        linked_at=utcnow(),
        team_id=team_id,
    )
    session.add(player)
    session.commit()
    session.refresh(player)
    return player


def make_snapshot(
    session: Session, player_id: int, captured_at: datetime, absolute_lp: int | None, queue: Queue = Queue.SOLO
) -> RankSnapshot:
    snapshot = RankSnapshot(
        player_id=player_id,
        queue=queue,
        tier="GOLD" if absolute_lp is not None else None,
        rank="IV" if absolute_lp is not None else None,
        lp=(absolute_lp or 0) % 100,
        absolute_lp=absolute_lp,
        captured_at=captured_at,
    )
    session.add(snapshot)
    session.commit()
    return snapshot


def make_participant(
    session: Session,
    player_id: int,
    match_id: str,
    game_start: datetime,
    duration: int,
    *,
    is_remake: bool = False,
    queue: Queue = Queue.SOLO,
) -> MatchParticipant:
    if session.get(Match, match_id) is None:
        session.add(
            Match(match_id=match_id, queue_id=420, game_start=game_start, game_duration=duration, raw_json="{}")
        )
    participant = MatchParticipant(
        match_id=match_id,
        player_id=player_id,
        queue=queue,
        game_start=game_start,
        game_duration=duration,
        is_remake=is_remake,
        champion_name="Ahri",
        win=True,
    )
    session.add(participant)
    session.commit()
    session.refresh(participant)
    return participant


async def demo_players(session: Session, demo_api: Any) -> list[Player]:
    """Deux joueurs liés via le client démo (puuid déterministe)."""
    players = []
    for display_name, riot_id in [("Mike", "La Peace#CHILL"), ("Jean", "Jean Bon#EUW")]:
        game_name, tag_line = riot_id.split("#")
        account = await demo_api.get_account_by_riot_id(game_name, tag_line)
        players.append(
            make_player(session, display_name, account.puuid, game_name=account.game_name, tag_line=account.tag_line)
        )
    return players


def event_types() -> set[str]:
    return {event["type"] for event in bus.recent(limit=200)}


def events_of(type_: str) -> list[dict]:
    return [event for event in bus.recent(limit=200) if event["type"] == type_]


# --------------------------------------------------------------------------- #
# Client Riot scripté (scénarios déterministes)
# --------------------------------------------------------------------------- #


class ScriptedAPI:
    """Implémentation minimale de `RiotAPI` entièrement pilotée par le test."""

    def __init__(self) -> None:
        self.request_count = 0
        self.entries: dict[str, list[LeagueEntryDTO]] = {}
        self.match_ids: dict[str, list[str]] = {}
        self.matches: dict[str, dict[str, Any]] = {}
        self.active: dict[str, ActiveGameDTO | None] = {}
        self.league_error: Exception | None = None
        self.calls: list[tuple] = []

    async def get_account_by_riot_id(self, game_name: str, tag_line: str) -> AccountDTO:
        self.request_count += 1
        raise RiotNotFound("compte inconnu", status=404)

    async def get_summoner_by_puuid(self, puuid: str) -> SummonerDTO:
        self.request_count += 1
        return SummonerDTO(puuid=puuid, summoner_id=None, profile_icon_id=None, summoner_level=None)

    async def get_league_entries_by_puuid(self, puuid: str) -> list[LeagueEntryDTO]:
        self.request_count += 1
        if self.league_error is not None:
            raise self.league_error
        return list(self.entries.get(puuid, []))

    async def get_match_ids_by_puuid(
        self, puuid: str, queue_id: int, start_time: int | None = None, count: int = 20
    ) -> list[str]:
        self.request_count += 1
        self.calls.append(("match_ids", puuid, queue_id, start_time, count))
        return list(self.match_ids.get(puuid, []))

    async def get_match(self, match_id: str) -> dict[str, Any]:
        self.request_count += 1
        return self.matches[match_id]

    async def get_active_game(self, puuid: str) -> ActiveGameDTO | None:
        self.request_count += 1
        return self.active.get(puuid)

    async def aclose(self) -> None:
        pass


def gold_iv(lp: int, wins: int = 10, losses: int = 8) -> list[LeagueEntryDTO]:
    return [
        LeagueEntryDTO(
            queue_type="RANKED_SOLO_5x5", tier="GOLD", rank="IV", league_points=lp, wins=wins, losses=losses
        )
    ]


def match_json(
    match_id: str,
    players: list[tuple[str, str, bool]],
    game_start: datetime,
    duration: int,
    queue_id: int = 420,
) -> dict[str, Any]:
    """JSON Match-V5 minimal : `players` = [(puuid, champion, win)] + figurants."""
    participants = [
        {
            "puuid": puuid,
            "championName": champion,
            "championId": 103,
            "teamPosition": "MIDDLE",
            "win": win,
            "kills": 7,
            "deaths": 2,
            "assists": 9,
            "totalMinionsKilled": 150,
            "neutralMinionsKilled": 20,
            "goldEarned": 12000,
            "totalDamageDealtToChampions": 20000,
            "visionScore": 25,
        }
        for puuid, champion, win in players
    ]
    participants += [
        {"puuid": f"stranger-{i}", "championName": "Garen", "championId": 86, "win": i % 2 == 0}
        for i in range(10 - len(participants))
    ]
    return {
        "metadata": {"matchId": match_id, "participants": [p["puuid"] for p in participants]},
        "info": {
            "gameId": int(match_id.split("_")[-1]),
            "queueId": queue_id,
            "gameMode": "CLASSIC",
            "gameStartTimestamp": int(game_start.timestamp() * 1000),
            "gameDuration": duration,
            "participants": participants,
        },
    }


# --------------------------------------------------------------------------- #
# Client démo : cycle complet
# --------------------------------------------------------------------------- #


async def test_demo_cycles_record_snapshots_matches_and_events(session: Session, demo_api: Any):
    make_challenge(session, ChallengeStatus.RUNNING, start_at=utcnow() - timedelta(hours=1))
    players = await demo_players(session, demo_api)
    poller = Poller(demo_api, bus, state)

    reports = []
    # Avec start_chance=1 et une durée nulle, quelques cycles suffisent pour voir des parties terminées
    for _ in range(8):
        reports.append(await poller.poll_once())
        await asyncio.sleep(0.02)
        session.expire_all()
        if len(reports) >= 3 and session.exec(select(MatchParticipant)).first() is not None:
            break

    assert all(report.errors == [] for report in reports), [r.errors for r in reports]
    assert reports[-1].players_polled == 2
    assert state.poll_count == len(reports)
    assert state.last_poll is reports[-1]
    assert state.polling is False

    session.expire_all()
    for player in players:
        snapshots = session.exec(
            select(RankSnapshot).where(RankSnapshot.player_id == player.id, RankSnapshot.queue == Queue.SOLO)
        ).all()
        assert snapshots, f"aucun snapshot pour {player.display_name}"
        assert snapshots[0].absolute_lp is not None  # le client démo donne un rang initial

    matches = session.exec(select(Match)).all()
    participants = session.exec(select(MatchParticipant)).all()
    assert matches and participants
    assert {mp.match_id for mp in participants} <= {m.match_id for m in matches}
    assert all(as_utc(m.game_start) >= utcnow() - timedelta(hours=2) for m in matches)

    # Un nouveau cycle ne duplique rien : (match_id, player_id) reste unique
    before_pairs = {(mp.match_id, mp.player_id) for mp in participants}
    await poller.poll_once()
    session.expire_all()
    participants = session.exec(select(MatchParticipant)).all()
    pairs = [(mp.match_id, mp.player_id) for mp in participants]
    assert len(pairs) == len(set(pairs))
    assert before_pairs <= set(pairs)

    types = event_types()
    assert {"poll_done", "rank_changed", "match_recorded"} <= types
    recorded = events_of("match_recorded")[0]["data"]
    assert {"match_id", "player_id", "display_name", "champion_name", "win", "kills", "game_start", "is_remake"} <= set(
        recorded
    )
    done = events_of("poll_done")[-1]["data"]
    assert done["players_polled"] == 2 and done["errors"] == []


async def test_registration_status_keeps_snapshots_but_no_matches(session: Session, demo_api: Any):
    make_challenge(session, ChallengeStatus.REGISTRATION)
    players = await demo_players(session, demo_api)
    poller = Poller(demo_api, bus, state)

    for _ in range(3):
        report = await poller.poll_once()
        assert report.errors == []
        await asyncio.sleep(0.02)

    session.expire_all()
    assert session.exec(select(Match)).all() == []
    assert session.exec(select(MatchParticipant)).all() == []
    for player in players:
        assert session.exec(select(RankSnapshot).where(RankSnapshot.player_id == player.id)).first() is not None
    assert "match_recorded" not in event_types()


# --------------------------------------------------------------------------- #
# API scriptée : rangs, parties, LP, spectator
# --------------------------------------------------------------------------- #


async def test_scripted_cycle_snapshots_live_matches_and_lp_change(session: Session):
    start_at = utcnow() - timedelta(hours=1)
    make_challenge(session, ChallengeStatus.RUNNING, start_at=start_at)
    team = Team(name="Duo Rouge", color="#ef4444", slot=1)
    session.add(team)
    session.commit()
    session.refresh(team)
    mike = make_player(session, "Mike", "p-mike", team_id=team.id)
    jean = make_player(session, "Jean", "p-jean")
    # Joueur non lié et joueur inactif : jamais pollés
    session.add(Player(display_name="Sans compte"))
    make_player(session, "Inactif", "p-inactif")
    inactive = session.exec(select(Player).where(Player.display_name == "Inactif")).one()
    inactive.active = False
    session.add(inactive)
    session.commit()

    api = ScriptedAPI()
    api.entries["p-mike"] = gold_iv(50)
    api.entries["p-jean"] = []  # unranked
    api.active["p-mike"] = ActiveGameDTO(
        game_id=1, game_start=utcnow(), queue_id=420, game_mode="CLASSIC", champion_id=103, champion_name="Ahri"
    )
    poller = Poller(api, bus, state)

    # --- cycle 1 : snapshots de référence + partie en cours détectée
    report = await poller.poll_once()
    assert report.errors == []
    assert report.players_polled == 2
    assert report.new_snapshots == 2
    assert report.new_matches == 0
    assert report.requests == 6  # 2 league + 2 match ids + 2 spectator
    assert report.duration_s >= 0

    session.expire_all()
    mike_snap = session.exec(select(RankSnapshot).where(RankSnapshot.player_id == mike.id)).one()
    assert (mike_snap.tier, mike_snap.rank, mike_snap.lp, mike_snap.absolute_lp) == ("GOLD", "IV", 50, 1250)
    jean_snap = session.exec(select(RankSnapshot).where(RankSnapshot.player_id == jean.id)).one()
    assert jean_snap.tier is None and jean_snap.absolute_lp is None

    assert set(state.live_games) == {mike.id}
    live = state.live_games[mike.id]
    assert (live.game_id, live.champion_name, live.queue_id) == (1, "Ahri", 420)
    live_starts = events_of("live_start")
    assert len(live_starts) == 1
    assert live_starts[0]["data"]["display_name"] == "Mike"
    assert live_starts[0]["data"]["champion_name"] == "Ahri"
    assert live_starts[0]["data"]["team_id"] == team.id
    assert live_starts[0]["data"]["ranked"] is True
    rank_events = events_of("rank_changed")
    assert {e["data"]["display_name"] for e in rank_events} == {"Mike", "Jean"}
    mike_rank_event = next(e for e in rank_events if e["data"]["player_id"] == mike.id)["data"]
    assert mike_rank_event["absolute_lp"] == 1250 and mike_rank_event["delta"] is None

    # Les IDs de parties sont demandés depuis le début du challenge, solo uniquement (flex désactivé)
    match_calls = [c for c in api.calls if c[0] == "match_ids"]
    assert {c[2] for c in match_calls} == {420}
    assert all(c[3] == int(start_at.timestamp()) for c in match_calls)

    # --- cycle 1 bis : rien ne change → aucun snapshot ni événement live supplémentaire
    report = await poller.poll_once()
    assert report.errors == [] and report.new_snapshots == 0 and report.new_matches == 0
    assert len(events_of("live_start")) == 1
    assert len(events_of("rank_changed")) == 2

    # --- cycle 2 : la partie se termine (victoire +20 LP), Jean y était aussi, plus une partie à part
    t_end = utcnow()
    await asyncio.sleep(0.01)
    api.entries["p-mike"] = gold_iv(70, wins=11)
    api.active["p-mike"] = None
    api.match_ids["p-mike"] = ["EUW1_1"]
    api.match_ids["p-jean"] = ["EUW1_1", "EUW1_2"]  # plus récente d'abord
    api.matches["EUW1_1"] = match_json(
        "EUW1_1", [("p-mike", "Ahri", True), ("p-jean", "Lux", False)], t_end - timedelta(seconds=1500), 1500
    )
    # Ancienne partie : durée en millisecondes → convertie en secondes
    api.matches["EUW1_2"] = match_json(
        "EUW1_2", [("p-jean", "Lux", True)], t_end - timedelta(seconds=2100), 1_800_000
    )

    report = await poller.poll_once()
    assert report.errors == []
    assert report.new_snapshots == 1
    assert report.new_matches == 2

    session.expire_all()
    matches = {m.match_id: m for m in session.exec(select(Match)).all()}
    assert set(matches) == {"EUW1_1", "EUW1_2"}
    assert matches["EUW1_1"].game_duration == 1500 and matches["EUW1_1"].queue_id == 420
    assert matches["EUW1_2"].game_duration == 1800
    assert '"championName":"Ahri"' in matches["EUW1_1"].raw_json

    participants = session.exec(select(MatchParticipant).order_by(MatchParticipant.id)).all()
    by_key = {(mp.match_id, mp.player_id): mp for mp in participants}
    assert set(by_key) == {("EUW1_1", mike.id), ("EUW1_1", jean.id), ("EUW1_2", jean.id)}
    mike_part = by_key[("EUW1_1", mike.id)]
    assert (mike_part.champion_name, mike_part.win, mike_part.position) == ("Ahri", True, "MIDDLE")
    assert (mike_part.kills, mike_part.deaths, mike_part.assists, mike_part.cs) == (7, 2, 9, 170)
    assert (mike_part.gold, mike_part.damage_to_champions, mike_part.vision_score) == (12000, 20000, 25)
    assert mike_part.queue == Queue.SOLO and mike_part.is_remake is False
    assert mike_part.lp_change == 20  # 1250 → 1270 entre les deux snapshots
    assert by_key[("EUW1_1", jean.id)].lp_change is None  # unranked : pas de LP calculables
    assert by_key[("EUW1_1", jean.id)].win is False

    recorded = events_of("match_recorded")
    assert {(e["data"]["match_id"], e["data"]["display_name"]) for e in recorded} == {
        ("EUW1_1", "Mike"),
        ("EUW1_1", "Jean"),
        ("EUW1_2", "Jean"),
    }
    mike_event = next(e for e in recorded if e["data"]["player_id"] == mike.id)["data"]
    assert mike_event["lp_change"] == 20 and mike_event["win"] is True and mike_event["team_id"] == team.id

    # Spectator : la partie a disparu → live_end
    assert state.live_games == {}
    live_ends = events_of("live_end")
    assert len(live_ends) == 1
    assert live_ends[0]["data"]["game_id"] == 1 and live_ends[0]["data"]["player_id"] == mike.id
    assert live_ends[0]["data"]["duration_s"] >= 0

    # --- cycle 3 : rien de nouveau → aucune duplication
    report = await poller.poll_once()
    assert report.errors == [] and report.new_matches == 0 and report.new_snapshots == 0
    session.expire_all()
    assert len(session.exec(select(MatchParticipant)).all()) == 3
    assert len(session.exec(select(Match)).all()) == 2
    assert len(events_of("match_recorded")) == 3
    assert state.poll_count == 4


async def test_errors_are_reported_not_raised(session: Session):
    make_challenge(session, ChallengeStatus.RUNNING, start_at=utcnow() - timedelta(hours=1))
    make_player(session, "Mike", "p-mike")
    make_player(session, "Jean", "p-jean")
    api = ScriptedAPI()
    poller = Poller(api, bus, state)

    api.league_error = RiotError("boom", status=500)
    report = await poller.poll_once()
    assert len(report.errors) == 2
    assert any("Mike" in error for error in report.errors)
    assert any("Jean" in error for error in report.errors)
    # Les autres étapes ont quand même tourné (match ids + spectator pour chaque joueur)
    assert report.requests == 6
    assert events_of("poll_done")[-1]["data"]["errors"] == report.errors
    assert state.polling is False

    # Clé invalide : le cycle s'arrête après la première erreur (une seule entrée)
    api.league_error = RiotUnauthorized("403 Forbidden", status=403)
    report = await poller.poll_once()
    assert len(report.errors) == 1
    assert "Mike" in report.errors[0]

    # Retour à la normale
    api.league_error = None
    api.entries["p-mike"] = gold_iv(50)
    report = await poller.poll_once()
    assert report.errors == []
    assert report.new_snapshots == 2  # Mike (Gold) + Jean (unranked)


async def test_concurrent_poll_once_share_one_cycle(session: Session):
    make_challenge(session, ChallengeStatus.REGISTRATION)
    make_player(session, "Mike", "p-mike")
    api = ScriptedAPI()
    api.entries["p-mike"] = gold_iv(10)
    poller = Poller(api, bus, state)

    first, second = await asyncio.gather(poller.poll_once(), poller.poll_once())
    assert first is second
    assert state.poll_count == 1
    assert len(events_of("poll_done")) == 1


async def test_run_forever_survives_exceptions(session: Session, monkeypatch: pytest.MonkeyPatch):
    make_challenge(session, ChallengeStatus.REGISTRATION)
    settings = Settings(
        riot_api_key="",
        riot_platform="euw1",
        riot_region="europe",
        demo_mode=True,
        poll_interval_seconds=0,
        track_flex=False,
        admin_password="x",
        database_url="sqlite://",
        games_per_day=10,
        timezone="Europe/Paris",
        discord_webhook_url="",
        base_url="http://localhost:8000",
    )
    poller = Poller(ScriptedAPI(), bus, state, settings=settings)
    calls = 0

    async def flaky_poll_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("panne passagère")
        return state.last_poll

    monkeypatch.setattr(poller, "poll_once", flaky_poll_once)
    task = asyncio.create_task(poller.run_forever())
    for _ in range(100):
        if calls >= 3:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls >= 3


# --------------------------------------------------------------------------- #
# backfill_lp_changes : calcul pur sur des lignes construites à la main
# --------------------------------------------------------------------------- #


async def test_backfill_lp_changes(session: Session):
    player = make_player(session, "Mike", "p-mike")
    t0 = utcnow() - timedelta(hours=3)
    minutes = lambda n: t0 + timedelta(minutes=n)  # noqa: E731

    # Snapshots : 1200 → 1220 → 1260 → unranked → 1300
    make_snapshot(session, player.id, minutes(0), 1200)
    make_snapshot(session, player.id, minutes(10), 1220)
    make_snapshot(session, player.id, minutes(30), 1260)
    make_snapshot(session, player.id, minutes(50), None)
    make_snapshot(session, player.id, minutes(70), 1300)

    # A : une seule partie entre S0 et S1 → +20
    game_a = make_participant(session, player.id, "EUW1_A", minutes(1), 330)  # fin à 6 min 30
    # B et C : deux parties entre S1 et S2 → indéterminable (None pour les deux)
    game_b = make_participant(session, player.id, "EUW1_B", minutes(11), 360)
    game_c = make_participant(session, player.id, "EUW1_C", minutes(18), 360)
    # D : snapshot « après » unranked → None
    game_d = make_participant(session, player.id, "EUW1_D", minutes(31), 360)
    # E : snapshot « avant » unranked → None
    game_e = make_participant(session, player.id, "EUW1_E", minutes(51), 360)
    # F : pas encore de snapshot après → None
    game_f = make_participant(session, player.id, "EUW1_F", minutes(71), 360)
    # R : remake entre S0 et S1 → ignoré, et n'empêche pas le calcul de A
    game_r = make_participant(session, player.id, "EUW1_R", minutes(8), 200, is_remake=True)
    # G : partie flex entre S0 et S1 (autre file : sans snapshot flex → None, et sans effet sur A)
    game_g = make_participant(session, player.id, "EUW1_G", minutes(2), 360, queue=Queue.FLEX)

    assert backfill_lp_changes(session, player) == 1
    session.expire_all()
    assert session.get(MatchParticipant, game_a.id).lp_change == 20
    for game in (game_b, game_c, game_d, game_e, game_f, game_r, game_g):
        assert session.get(MatchParticipant, game.id).lp_change is None, game.match_id

    # Un snapshot arrive après F → F devient calculable au passage suivant ; A n'est pas recalculé
    make_snapshot(session, player.id, minutes(90), 1283)
    assert backfill_lp_changes(session, player) == 1
    session.expire_all()
    assert session.get(MatchParticipant, game_f.id).lp_change == -17
    assert session.get(MatchParticipant, game_a.id).lp_change == 20
    assert backfill_lp_changes(session, player) == 0
