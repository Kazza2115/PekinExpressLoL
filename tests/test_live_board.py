"""Partie en cours : composition complète (Spectator-V5), tableau des 10 joueurs, démo cohérente,
et personnes connectées au site (présence)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import RankSnapshot, Team
from app.events import bus
from app.presence import LEAVE_GRACE_S, MAX_CLIENTS, TTL_HIDDEN_S, TTL_VISIBLE_S, PresenceRegistry
from app.riot import demo as demo_module
from app.riot.base import ActiveGameDTO
from app.riot.demo import DemoRiotClient
from app.riot.endpoints import parse_active_game
from app.services.poller import Poller
from app.services.scoreboard import build_live_board
from app.state import LiveGameState, state
from tests.test_poller import ScriptedAPI, make_challenge, make_player

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def spectator_payload(puuids: list[str], game_id: int = 77) -> dict[str, Any]:
    """Réponse Spectator-V5 réaliste : 10 joueurs, runes, Riot ID, un ban vide (-1)."""
    participants = []
    for index in range(10):
        puuid = puuids[index] if index < len(puuids) else f"stranger-{index}"
        participants.append(
            {
                "puuid": puuid,
                "riotId": f"Joueur{index}#EUW" if index != 9 else "",
                "teamId": 100 if index < 5 else 200,
                "championId": [62, 103, 64, 222, 412, 86, 238, 121, 21, 40][index],
                "spell1Id": 4,
                "spell2Id": 11 if index in (2, 7) else 14,
                "perks": {"perkIds": [8010, 9111, 9104, 8299, 8473, 8242, 5005, 5008, 5001], "perkStyle": 8000, "perkSubStyle": 8400},
                "profileIconId": 29,
                "bot": False,
            }
        )
    return {
        "gameId": game_id,
        "mapId": 11,
        "gameMode": "CLASSIC",
        "gameQueueConfigId": 420,
        "gameStartTime": int((NOW - timedelta(minutes=12)).timestamp() * 1000),
        "gameLength": 700,
        "participants": participants,
        "bannedChampions": [
            {"championId": 157, "teamId": 100, "pickTurn": 1},
            {"championId": -1, "teamId": 200, "pickTurn": 6},
            {"championId": 777, "teamId": 200, "pickTurn": 7},
        ],
    }


# --------------------------------------------------------------------------- #
# Spectator-V5 → DTO
# --------------------------------------------------------------------------- #


def test_parse_active_game_keeps_the_whole_board():
    game = parse_active_game(spectator_payload(["p-mike"]), "p-mike")
    assert game.champion_id == 62 and game.map_id == 11
    assert len(game.participants) == 10
    first = game.participants[0]
    assert (first.riot_name, first.riot_tag, first.team_id, first.spell_ids) == ("Joueur0", "EUW", 100, (4, 14))
    assert (first.keystone_id, first.primary_style_id, first.sub_style_id) == (8010, 8000, 8400)
    assert game.participants[9].riot_name == "" and game.participants[9].riot_tag is None  # Riot ID masqué : « Joueur masqué » à l'écran
    assert [(b.team_id, b.champion_id) for b in game.bans] == [(100, 157), (200, 777)]  # -1 : pas de ban
    assert game.champions_by_puuid["p-mike"] == 62


def test_parse_active_game_tolerates_missing_fields():
    game = parse_active_game({"gameId": 5, "participants": [{"puuid": "x"}, "pas un dict"]}, "x")
    assert len(game.participants) == 1
    assert game.participants[0].riot_name == "" and game.participants[0].keystone_id is None
    assert game.bans == [] and game.map_id is None


# --------------------------------------------------------------------------- #
# Tableau de la partie (JSON de l'API)
# --------------------------------------------------------------------------- #


def live_state(player_id: int, board: ActiveGameDTO | None, *, start: datetime | None = None, game_id: int = 77) -> LiveGameState:
    return LiveGameState(
        player_id=player_id,
        game_id=game_id,
        champion_id=62,
        champion_name="MonkeyKing",
        queue_id=420,
        game_mode="CLASSIC",
        game_start=start or datetime.fromtimestamp(0, tz=timezone.utc),
        detected_at=NOW - timedelta(minutes=3),
        board=board,
    )


def test_build_live_board_highlights_challenge_players(session: Session):
    team = Team(id=1, name="Duo Rouge", color="#ef4444", slot=1)
    session.add(team)
    session.commit()
    mike = make_player(session, "Mike", "p-mike", team_id=1)
    lea = make_player(session, "Léa", "p-lea", team_id=1)
    hugo = make_player(session, "Hugo", "p-hugo")
    game = parse_active_game(spectator_payload(["p-mike", "p-lea", "s2", "s3", "s4", "p-hugo"]), "p-mike")
    for participant in game.participants:
        participant.champion_name = {62: "MonkeyKing", 103: "Ahri"}.get(participant.champion_id)
    snapshots = {mike.id: RankSnapshot(player_id=mike.id, tier="DIAMOND", rank="III", lp=38)}
    lives = [live_state(mike.id, game, start=NOW - timedelta(minutes=12)), live_state(lea.id, game)]
    board = build_live_board(
        lives, {p.puuid: p for p in (mike, lea, hugo)}, {1: team}, snapshots, NOW
    )
    assert board["game_id"] == 77 and board["queue_label"] == "Classée Solo/Duo"
    assert board["loading"] is False and board["elapsed_s"] == 12 * 60
    assert [t["side"] for t in board["teams"]] == ["blue", "red"]
    blue = board["teams"][0]
    assert blue["has_challenge_player"] is True
    assert [b["champion_name"] for b in blue["bans"]] == ["Champion 157"]  # nom inconnu hors ligne
    row = blue["players"][0]
    assert row["is_challenge"] and row["display_name"] == "Mike" and row["team_color"] == "#ef4444"
    assert row["champion_name"] == "Wukong" and row["champion_icon_url"].endswith("/MonkeyKing.png")
    assert row["rank_label"] == "Diamond III · 38 LP"
    assert len(row["spell_urls"]) == 2 and row["keystone"]
    stranger = blue["players"][2]
    assert stranger["is_challenge"] is False and stranger["player_id"] is None and stranger["riot_name"] == "Joueur2"
    assert {cp["display_name"] for cp in board["challenge_players"]} == {"Mike", "Léa", "Hugo"}
    assert board["duo_together"] is True and board["versus"] is True  # Hugo est en face
    assert board["in_game_player_ids"] == sorted([mike.id, lea.id])


def test_build_live_board_without_composition_still_names_the_players(session: Session):
    mike = make_player(session, "Mike", "p-mike")
    board = build_live_board([live_state(mike.id, None)], {"p-mike": mike}, {}, {}, NOW)
    assert board["teams"] == [] and board["loading"] is True
    assert board["elapsed_s"] == 180  # écran de chargement : durée depuis la détection
    assert [cp["display_name"] for cp in board["challenge_players"]] == ["Mike"]


# --------------------------------------------------------------------------- #
# Poller : un seul tableau par partie, noms des champions résolus
# --------------------------------------------------------------------------- #


async def test_poller_shares_one_board_between_players_of_the_same_game(session: Session, monkeypatch: pytest.MonkeyPatch):
    from app.db.models import ChallengeStatus

    make_challenge(session, ChallengeStatus.RUNNING, start_at=NOW - timedelta(hours=1))
    mike = make_player(session, "Mike", "p-mike")
    lea = make_player(session, "Léa", "p-lea")
    api = ScriptedAPI()
    for puuid in ("p-mike", "p-lea"):
        api.active[puuid] = parse_active_game(spectator_payload(["p-mike", "p-lea"]), puuid)

    async def champion_name(champion_id: int) -> str | None:
        return {62: "MonkeyKing", 103: "Ahri", 157: "Yasuo"}.get(champion_id)

    monkeypatch.setattr("app.riot.ddragon.champion_name_from_id", champion_name)
    await Poller(api, bus, state).poll_live_once()
    assert set(state.live_games) == {mike.id, lea.id}
    board = state.live_games[mike.id].board
    assert board is not None and board is state.live_games[lea.id].board  # partagé, pas recopié
    assert board.participants[0].champion_name == "MonkeyKing" and board.bans[0].champion_name == "Yasuo"


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #


def test_api_live_returns_one_board_per_game(client: TestClient, session: Session):
    mike = make_player(session, "Mike", "p-mike")
    lea = make_player(session, "Léa", "p-lea")
    game = parse_active_game(spectator_payload(["p-mike", "p-lea"]), "p-mike")
    state.live_games[mike.id] = live_state(mike.id, game, start=NOW)
    state.live_games[lea.id] = live_state(lea.id, game, start=NOW)
    data = client.get("/api/live").json()
    assert {item["game_id"] for item in data["live"]} == {77}
    assert len(data["games"]) == 1
    assert {cp["display_name"] for cp in data["games"][0]["challenge_players"]} == {"Mike", "Léa"}
    assert sum(len(t["players"]) for t in data["games"][0]["teams"]) == 10
    profile = client.get(f"/api/players/{mike.id}").json()
    assert profile["live_game"]["game_id"] == 77
    assert profile["stats"]["live"]["game_id"] == 77


# --------------------------------------------------------------------------- #
# Client démo : 10 joueurs dès le début, mêmes joueurs dans le tableau final
# --------------------------------------------------------------------------- #


async def test_demo_live_board_matches_the_final_scoreboard():
    api = DemoRiotClient(start_chance=1.0, game_duration_range=(0, 0), rng=random.Random(3), remake_chance=0.0)
    account = await api.get_account_by_riot_id("Mike", "DEMO")
    live = await api.get_active_game(account.puuid)
    assert live is not None and len(live.participants) == 10
    assert sorted(p.team_id for p in live.participants) == [100] * 5 + [200] * 5
    assert len({p.champion_id for p in live.participants}) == 10
    assert len(live.bans) == 10 and not {b.champion_id for b in live.bans} & {p.champion_id for p in live.participants}
    me = next(p for p in live.participants if p.puuid == account.puuid)
    assert me.champion_id == live.champion_id and me.position
    roster = {(p.puuid, p.champion_id, p.spell_ids) for p in live.participants}
    for _ in range(10):  # la partie se termine au bout de quelques ticks
        if await api.get_active_game(account.puuid) is None:
            break
    match = await api.get_match((await api.get_match_ids_by_puuid(account.puuid, 420, count=1))[0])
    final = {(p["puuid"], p["championId"], (p["summoner1Id"], p["summoner2Id"])) for p in match["info"]["participants"]}
    assert final == roster
    bans = [b["championId"] for t in match["info"]["teams"] for b in t["bans"]]
    assert sorted(bans) == sorted(b.champion_id for b in live.bans)


async def test_demo_sometimes_puts_two_challenge_players_in_the_same_game(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(demo_module, "SHARED_GAME_CHANCE", 1.0)
    monkeypatch.setattr(demo_module, "SHARED_SAME_TEAM_CHANCE", 1.0)
    api = DemoRiotClient(start_chance=1.0, game_duration_range=(0, 0), rng=random.Random(5), remake_chance=0.0)
    mike = await api.get_account_by_riot_id("Mike", "DEMO")
    lea = await api.get_account_by_riot_id("Lea", "EUW")
    first = await api.get_active_game(mike.puuid)
    assert first is not None and lea.puuid in first.champions_by_puuid
    second = await api.get_active_game(lea.puuid)
    assert second is not None and second.game_id == first.game_id
    assert second.champion_id == first.champions_by_puuid[lea.puuid] != first.champion_id
    for _ in range(10):
        if await api.get_active_game(mike.puuid) is None:
            break
    assert await api.get_active_game(lea.puuid) is None  # partie terminée pour les deux
    mike_ids = await api.get_match_ids_by_puuid(mike.puuid, 420, count=5)
    lea_ids = await api.get_match_ids_by_puuid(lea.puuid, 420, count=5)
    assert mike_ids[0] == lea_ids[0]
    match = await api.get_match(mike_ids[0])
    wins = {p["puuid"]: p["win"] for p in match["info"]["participants"]}
    assert wins[mike.puuid] == wins[lea.puuid]  # même équipe : même résultat
    entries = {puuid: (await api.get_league_entries_by_puuid(puuid))[0] for puuid in (mike.puuid, lea.puuid)}
    assert all(e.wins + e.losses > 0 for e in entries.values())


# --------------------------------------------------------------------------- #
# Présence : registre en mémoire
# --------------------------------------------------------------------------- #


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_presence_counts_browsers_and_identified_players_once():
    clock = Clock()
    reg = PresenceRegistry(clock=clock)
    assert reg.touch("browser-aaaa", "tab-1111", "duos", None, True)
    reg.touch("browser-aaaa", "tab-2222", "dashboard", None, True)  # 2e onglet : même visiteur
    reg.touch("browser-bbbb", "tab-3333", "home", 3, True)
    reg.touch("phone-cccccc", "tab-4444", "player", 3, False)  # même joueur sur son téléphone
    snap = reg.snapshot()
    assert snap.anonymous == 1 and set(snap.players) == {3}
    assert snap.players[3] == {"active": True, "page": "home"}
    assert not reg.touch("x", "tab", "home", None, True)  # identifiant invalide : ignoré
    assert not reg.touch(None, None, None, None, True)


def test_presence_expires_hidden_tabs_later_and_leave_is_quick():
    clock = Clock()
    reg = PresenceRegistry(clock=clock)
    reg.touch("visible-aaaa", "tab-aaaa", "duos", None, True)
    reg.touch("hidden-bbbbb", "tab-bbbb", "duos", 7, False)
    clock.t += TTL_VISIBLE_S + 1
    snap = reg.snapshot()
    assert snap.anonymous == 0 and snap.players[7]["active"] is False  # arrière-plan : encore là
    clock.t += TTL_HIDDEN_S
    assert reg.snapshot().players == {}
    reg.touch("leaver-cccc", "tab-cccc", "home", None, True)
    reg.leave("leaver-cccc", "tab-cccc")
    assert reg.snapshot().anonymous == 1  # page suivante du site peut-être en chargement
    clock.t += LEAVE_GRACE_S + 0.5
    assert reg.snapshot().anonymous == 0  # fermé pour de bon
    reg.touch("leaver-dddd", "tab-dddd", "home", None, True)
    reg.leave("leaver-dddd", "tab-dddd")
    clock.t += 1
    reg.touch("leaver-dddd", "tab-eeee", "duos", None, True)  # la page suivante s'est manifestée
    clock.t += LEAVE_GRACE_S
    assert reg.snapshot().anonymous == 1


def test_presence_registry_is_bounded():
    clock = Clock()
    reg = PresenceRegistry(clock=clock)
    for index in range(MAX_CLIENTS + 20):
        clock.t += 0.01
        reg.touch(f"client-{index:06d}", "tab-0000", "home", None, True)
    assert reg.snapshot().anonymous == MAX_CLIENTS
    reg.touch("client-zzzzzz", "tab-0000", "no-such-page", None, True)
    assert reg.snapshot().anonymous == MAX_CLIENTS


def test_presence_flood_never_evicts_identified_players():
    clock = Clock()
    reg = PresenceRegistry(clock=clock)
    reg.touch("real-player1", "tab-0000", "duos", 4, True)
    for index in range(MAX_CLIENTS * 2):
        clock.t += 0.01
        reg.touch(f"flood-{index:06d}", "tab-0000", "home", None, True)
    snap = reg.snapshot()
    assert set(snap.players) == {4} and snap.anonymous == MAX_CLIENTS - 1


def test_events_poll_reports_who_is_online(client: TestClient, session: Session):
    mike = make_player(session, "Mike", "p-mike")
    first = client.get("/api/events/recent?cid=browser-aaaa&tab=tab-1111&page=duos&vis=1").json()
    assert first["presence"] == {"online": 1, "anonymous": 1, "players": []} and first["me"] is None
    second = client.get(f"/api/events/recent?since=0&cid=browser-bbbb&tab=tab-2222&page=dashboard&vis=1&me={mike.id}")
    assert second.headers["cache-control"] == "no-store"
    data = second.json()
    assert data["me"] == {"player_id": mike.id, "display_name": "Mike"}
    assert data["presence"]["online"] == 2
    person = data["presence"]["players"][0]
    assert (person["display_name"], person["page"], person["active"], person["in_game"]) == ("Mike", "dashboard", True, False)
    assert "cid" not in str(data["presence"]) and "browser-" not in str(data["presence"])
    # Valeurs farfelues : jamais d'erreur (sinon la page croirait le site hors ligne)
    odd = client.get("/api/events/recent?cid=%21%21&tab=&page=%3Cscript%3E&vis=x&me=abc")
    assert odd.status_code == 200 and odd.json()["me"] is None
    assert client.get("/api/events/recent?me=99999999999999999999").status_code == 200
    # Fermeture de l'onglet : signalée (retirée au bout de quelques secondes sans nouvelle)
    assert client.post("/api/presence/leave", content=b'{"cid":"browser-aaaa","tab":"tab-1111"}').status_code == 204
    assert client.post("/api/presence/leave", content=b"pas du json").status_code == 204
    after = client.get("/api/events/recent?cid=browser-bbbb&tab=tab-2222&page=dashboard&vis=1").json()
    assert after["presence"]["players"] == []  # Mike redevenu anonyme


# --------------------------------------------------------------------------- #
# Corrections de la relecture
# --------------------------------------------------------------------------- #


def test_duo_on_opposite_sides_is_a_duel_not_a_duo(session: Session):
    team = Team(id=1, name="Duo Rouge", color="#ef4444", slot=1)
    session.add(team)
    session.commit()
    mike = make_player(session, "Mike", "p-mike", team_id=1)
    lea = make_player(session, "Léa", "p-lea", team_id=1)
    payload = spectator_payload(["p-mike", "s1", "s2", "s3", "s4", "p-lea"])  # Léa en face (équipe rouge)
    game = parse_active_game(payload, "p-mike")
    board = build_live_board([live_state(mike.id, game), live_state(lea.id, game)], {"p-mike": mike, "p-lea": lea}, {1: team}, {}, NOW)
    assert board["duo_together"] is False and board["versus"] is True


async def test_discord_live_announce_keeps_opponents_separate(session: Session, monkeypatch: pytest.MonkeyPatch):
    from app.db.models import ChallengeStatus
    from tests.test_discord_embeds import make_settings

    make_challenge(session, ChallengeStatus.RUNNING, start_at=NOW - timedelta(hours=1))
    team = Team(name="Duo Rouge", color="#ef4444", slot=1)
    session.add(team)
    session.commit()
    session.refresh(team)
    make_player(session, "Mike", "p-mike", team_id=team.id)
    make_player(session, "Léa", "p-lea", team_id=team.id)
    sent: list[str] = []

    async def fake_send(content: str, **_: Any) -> bool:
        sent.append(content)
        return True

    monkeypatch.setattr("app.services.poller.send_discord", fake_send)
    api = ScriptedAPI()
    payload = spectator_payload(["p-mike", "s1", "s2", "s3", "s4", "p-lea"])
    for puuid in ("p-mike", "p-lea"):
        api.active[puuid] = parse_active_game(payload, puuid)
    await Poller(api, bus, state, settings=make_settings()).poll_live_once()
    assert len(sent) == 2 and not any("en duo" in content for content in sent)


async def test_real_start_after_loading_screen_is_published(session: Session):
    from app.db.models import ChallengeStatus

    make_challenge(session, ChallengeStatus.RUNNING, start_at=NOW - timedelta(hours=1))
    mike = make_player(session, "Mike", "p-mike")
    api = ScriptedAPI()
    loading = spectator_payload(["p-mike"])
    loading["gameStartTime"] = 0  # écran de chargement
    api.active["p-mike"] = parse_active_game(loading, "p-mike")
    poller = Poller(api, bus, state)
    await poller.poll_live_once()
    before = len([e for e in bus.recent(limit=500) if e["type"] == "live_update"])
    api.active["p-mike"] = parse_active_game(spectator_payload(["p-mike"]), "p-mike")
    await poller.poll_live_once()
    updates = [e for e in bus.recent(limit=500) if e["type"] == "live_update"]
    assert len(updates) == before + 1 and updates[-1]["data"] == {"player_id": mike.id, "game_id": 77}
    assert state.live_games[mike.id].game_start.timestamp() > 0


async def test_demo_shared_game_is_seen_live_by_both_players(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(demo_module, "SHARED_GAME_CHANCE", 1.0)
    api = DemoRiotClient(start_chance=1.0, game_duration_range=(0, 0), rng=random.Random(9), remake_chance=0.0)
    mike = await api.get_account_by_riot_id("Mike", "DEMO")
    lea = await api.get_account_by_riot_id("Lea", "EUW")
    # Ordre d'un cycle : parties (Match-V5) de chacun, puis Spectator de chacun
    await api.get_match_ids_by_puuid(mike.puuid, 420)
    await api.get_match_ids_by_puuid(lea.puuid, 420)
    seen_mike = await api.get_active_game(mike.puuid)
    seen_lea = await api.get_active_game(lea.puuid)
    assert seen_mike is not None and seen_lea is not None and seen_mike.game_id == seen_lea.game_id
