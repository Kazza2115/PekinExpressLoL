"""`DemoRiotClient` déterministe : résolution, rangs, parties simulées, filtres, identifiants."""

from __future__ import annotations

import random
import time

import pytest

from app.riot.base import ActiveGameDTO, RiotNotFound
from app.riot.demo import CHAMPION_POOL, DemoRiotClient, demo_puuid
from app.services.registration import parse_riot_id
from app.services.stats import DIVISIONS, TIERS, absolute_lp


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def api() -> DemoRiotClient:
    return DemoRiotClient(start_chance=1.0, game_duration_range=(0, 0), rng=random.Random(1))


async def play_games(api: DemoRiotClient, puuid: str, games: int, max_calls: int = 60) -> list[str]:
    """Fait avancer la simulation jusqu'à ce que `games` nouvelles parties soient terminées."""
    before = set(await api.get_match_ids_by_puuid(puuid, 420, count=100))
    new_ids: list[str] = []
    for _ in range(max_calls):
        ids = await api.get_match_ids_by_puuid(puuid, 420, count=100)
        new_ids = [i for i in ids if i not in before]
        if len(new_ids) >= games:
            return new_ids
    raise AssertionError(f"seulement {len(new_ids)} parties après {max_calls} appels")


@pytest.mark.anyio
async def test_resolve_riot_id_is_deterministic(api):
    account = await api.get_account_by_riot_id("La Peace", "CHILL")
    assert account.puuid.startswith("demo-") and len(account.puuid) == len("demo-") + 24
    assert account.puuid == demo_puuid("La Peace", "CHILL")
    assert (account.game_name, account.tag_line) == ("La Peace", "CHILL")
    again = await api.get_account_by_riot_id("La Peace", "CHILL")
    assert again.puuid == account.puuid
    other = await api.get_account_by_riot_id("Autre", "EUW")
    assert other.puuid != account.puuid
    assert api.request_count == 3


@pytest.mark.anyio
async def test_summoner_and_league_entries(api):
    account = await api.get_account_by_riot_id("La Peace", "CHILL")
    summoner = await api.get_summoner_by_puuid(account.puuid)
    assert summoner.puuid == account.puuid
    assert summoner.profile_icon_id is not None and 0 <= summoner.profile_icon_id <= 28
    assert summoner.summoner_level and summoner.summoner_level >= 30

    entries = await api.get_league_entries_by_puuid(account.puuid)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.queue_type == "RANKED_SOLO_5x5"
    assert TIERS.index("SILVER") <= TIERS.index(entry.tier) <= TIERS.index("DIAMOND")
    assert entry.rank in DIVISIONS
    assert 0 <= entry.league_points < 100
    assert entry.wins >= 0 and entry.losses >= 0

    # Rang initial déterministe : un autre client donne le même rang pour le même puuid
    other_client = DemoRiotClient(rng=random.Random(99))
    same = await other_client.get_league_entries_by_puuid(account.puuid)
    assert (same[0].tier, same[0].rank, same[0].league_points) == (entry.tier, entry.rank, entry.league_points)


@pytest.mark.anyio
async def test_unranked_account_has_no_entries(api):
    account = await api.get_account_by_riot_id("unranked", "EUW")
    assert await api.get_league_entries_by_puuid(account.puuid) == []
    summoner = await api.get_summoner_by_puuid(account.puuid)
    assert summoner.profile_icon_id is not None


@pytest.mark.anyio
async def test_live_game_then_match_appears(api):
    account = await api.get_account_by_riot_id("La Peace", "CHILL")
    puuid = account.puuid
    assert await api.get_match_ids_by_puuid(puuid, 420) == []  # 1er tick : démarre une partie

    live = await api.get_active_game(puuid)  # 2e tick : la partie reste visible
    assert isinstance(live, ActiveGameDTO)
    assert live.champion_name in {name for name, _ in CHAMPION_POOL}
    assert live.champion_id > 0 and live.queue_id == 420 and live.game_mode == "CLASSIC"
    assert abs(live.game_start.timestamp() - time.time()) < 5
    assert live.game_id == 1

    ids = await api.get_match_ids_by_puuid(puuid, 420)  # 3e tick : la partie se termine
    assert ids == ["DEMO_000001"]
    assert await api.get_active_game(puuid) is None  # tick de pause : plus en partie

    match = await api.get_match("DEMO_000001")
    assert match["metadata"]["matchId"] == "DEMO_000001"
    assert len(match["metadata"]["participants"]) == 10
    assert puuid in match["metadata"]["participants"]
    info = match["info"]
    assert info["queueId"] == 420 and info["gameMode"] == "CLASSIC" and info["gameId"] == 1
    assert len(info["participants"]) == 10
    assert info["gameStartTimestamp"] + info["gameDuration"] * 1000 == info["gameEndTimestamp"]
    assert abs(info["gameEndTimestamp"] / 1000 - time.time()) < 5

    me = next(p for p in info["participants"] if p["puuid"] == puuid)
    assert me["championName"] == live.champion_name and me["championId"] == live.champion_id
    assert me["riotIdGameName"] == "La Peace" and me["riotIdTagline"] == "CHILL"
    assert me["teamId"] in (100, 200) and me["teamPosition"] in {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}
    for key in ("win", "kills", "deaths", "assists", "totalMinionsKilled", "neutralMinionsKilled",
                "goldEarned", "totalDamageDealtToChampions", "visionScore", "champLevel", "summonerName"):
        assert key in me
    # 5 joueurs par équipe, un par poste, champions tous différents
    for team_id in (100, 200):
        members = [p for p in info["participants"] if p["teamId"] == team_id]
        assert len(members) == 5
        assert {p["teamPosition"] for p in members} == {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}
        assert all(p["win"] == (team_id == next(t["teamId"] for t in info["teams"] if t["win"])) for p in members)
    assert len({p["championName"] for p in info["participants"]}) == 10
    assert len({p["puuid"] for p in info["participants"]}) == 10


@pytest.mark.anyio
async def test_lp_and_record_change_after_matches(api):
    account = await api.get_account_by_riot_id("Lealicious", "EUW")
    puuid = account.puuid
    before = (await api.get_league_entries_by_puuid(puuid))[0]
    before_abs = absolute_lp(before.tier, before.rank, before.league_points)

    new_ids = await play_games(api, puuid, games=6)
    matches = [await api.get_match(match_id) for match_id in new_ids]
    remakes = [m for m in matches if m["info"]["gameDuration"] < 300]
    normal = [m for m in matches if m["info"]["gameDuration"] >= 300]
    assert normal, "au moins une partie normale attendue"
    for match in remakes:
        assert match["info"]["gameDuration"] == 180
        assert all(p["win"] is False for p in match["info"]["participants"])

    after = (await api.get_league_entries_by_puuid(puuid))[0]
    after_abs = absolute_lp(after.tier, after.rank, after.league_points)
    wins = sum(1 for m in normal if next(p for p in m["info"]["participants"] if p["puuid"] == puuid)["win"])
    losses = len(normal) - wins
    assert after.wins == before.wins + wins
    assert after.losses == before.losses + losses
    assert after_abs is not None and before_abs is not None
    # Chaque partie normale vaut ±14..26 LP
    assert 14 * len(normal) <= abs(after_abs - before_abs) + 26 * len(normal)
    if wins != losses:
        assert after_abs != before_abs
    assert 0 <= after.league_points < 100 or after.tier == "MASTER"


@pytest.mark.anyio
async def test_promotion_and_demotion_handled():
    api = DemoRiotClient(start_chance=1.0, game_duration_range=(0, 0), rng=random.Random(7), win_chance=1.0, remake_chance=0.0)
    account = await api.get_account_by_riot_id("Promu", "EUW")
    before = (await api.get_league_entries_by_puuid(account.puuid))[0]
    await play_games(api, account.puuid, games=12)
    after = (await api.get_league_entries_by_puuid(account.puuid))[0]
    assert absolute_lp(after.tier, after.rank, after.league_points) > absolute_lp(before.tier, before.rank, before.league_points)
    assert (after.tier, after.rank) != (before.tier, before.rank)  # 12 victoires ≥ 168 LP : division changée
    assert after.hot_streak is True

    loser = DemoRiotClient(start_chance=1.0, game_duration_range=(0, 0), rng=random.Random(7), win_chance=0.0, remake_chance=0.0)
    before = (await loser.get_league_entries_by_puuid(account.puuid))[0]
    await play_games(loser, account.puuid, games=12)
    after = (await loser.get_league_entries_by_puuid(account.puuid))[0]
    assert absolute_lp(after.tier, after.rank, after.league_points) < absolute_lp(before.tier, before.rank, before.league_points)
    assert after.hot_streak is False and after.losses == before.losses + 12


@pytest.mark.anyio
async def test_match_ids_filters(api):
    account = await api.get_account_by_riot_id("La Peace", "CHILL")
    puuid = account.puuid
    ids = await play_games(api, puuid, games=3)
    assert len(ids) >= 3

    assert await api.get_match_ids_by_puuid(puuid, 440) == []  # file flex : rien
    assert await api.get_match_ids_by_puuid(puuid, 420, start_time=int(time.time()) + 1000) == []
    assert len(await api.get_match_ids_by_puuid(puuid, 420, start_time=0)) >= 3
    assert len(await api.get_match_ids_by_puuid(puuid, 420, count=2)) == 2

    # Filtre sur gameEndTimestamp : seules les parties terminées après `start_time` sont renvoyées
    all_ids = await api.get_match_ids_by_puuid(puuid, 420, count=100)
    ends = {match_id: (await api.get_match(match_id))["info"]["gameEndTimestamp"] // 1000 for match_id in all_ids}
    threshold = sorted(ends.values())[1]
    filtered = await api.get_match_ids_by_puuid(puuid, 420, start_time=threshold, count=100)
    assert all(ends.get(i, threshold) >= threshold for i in filtered)
    assert all(i in filtered for i, s in ends.items() if s >= threshold)
    assert all(i not in filtered for i, s in ends.items() if s < threshold)
    # Pagination : `start` décale la fenêtre
    assert await api.get_match_ids_by_puuid(puuid, 420, count=100, start=1) == all_ids[1:]

    # Plus récentes d'abord
    recent = await api.get_match_ids_by_puuid(puuid, 420, count=100)
    ends = [(await api.get_match(i))["info"]["gameEndTimestamp"] for i in recent]
    assert ends == sorted(ends, reverse=True)


@pytest.mark.anyio
async def test_start_time_filters_on_game_end(api):
    """`start_time` (début du challenge) filtre sur la FIN de partie : les premières parties
    du challenge, commencées fictivement avant, sont renvoyées et leur fin est ≥ start_time."""
    account = await api.get_account_by_riot_id("Fenetre", "EUW")
    puuid = account.puuid
    challenge_start = int(time.time()) - 5
    ids: list[str] = []
    for _ in range(12):
        ids = await api.get_match_ids_by_puuid(puuid, 420, start_time=challenge_start)
        if len(ids) >= 2:
            break
    assert len(ids) >= 2
    for match_id in ids:
        info = (await api.get_match(match_id))["info"]
        assert info["gameEndTimestamp"] // 1000 >= challenge_start
        assert info["gameEndTimestamp"] <= int(time.time() * 1000) + 1000  # jamais dans le futur
        assert info["gameDuration"] >= 180
        assert info["gameStartTimestamp"] + info["gameDuration"] * 1000 == info["gameEndTimestamp"]
    # Un début de fenêtre dans le futur ne renvoie rien
    assert await api.get_match_ids_by_puuid(puuid, 420, start_time=int(time.time()) + 3600) == []


@pytest.mark.anyio
async def test_match_ids_unique_and_monotonic(api):
    first = await api.get_account_by_riot_id("La Peace", "CHILL")
    second = await api.get_account_by_riot_id("Lealicious", "EUW")
    for _ in range(12):
        await api.get_match_ids_by_puuid(first.puuid, 420)
        await api.get_match_ids_by_puuid(second.puuid, 420)
    ids_first = await api.get_match_ids_by_puuid(first.puuid, 420, count=100)
    ids_second = await api.get_match_ids_by_puuid(second.puuid, 420, count=100)
    assert ids_first and ids_second
    assert not set(ids_first) & set(ids_second)
    all_ids = ids_first + ids_second
    assert len(set(all_ids)) == len(all_ids)
    assert all(i.startswith("DEMO_") and i[5:].isdigit() and len(i) == 11 for i in all_ids)
    # Chaque joueur voit ses parties par identifiant décroissant (ids monotones)
    for ids in (ids_first, ids_second):
        numbers = [int(i[5:]) for i in ids]
        assert numbers == sorted(numbers, reverse=True)
    with pytest.raises(RiotNotFound):
        await api.get_match("DEMO_999999")


@pytest.mark.anyio
async def test_no_game_starts_when_start_chance_is_zero():
    api = DemoRiotClient(start_chance=0.0, rng=random.Random(3))
    account = await api.get_account_by_riot_id("Casanier", "EUW")
    for _ in range(5):
        assert await api.get_active_game(account.puuid) is None
    assert await api.get_match_ids_by_puuid(account.puuid, 420) == []
    assert api.request_count == 7
    await api.aclose()


def test_seed_names_are_valid_riot_ids():
    assert len(DemoRiotClient.seed_names) == 8
    riot_ids = [riot_id for _, riot_id in DemoRiotClient.seed_names]
    assert len(set(riot_ids)) == 8
    assert len({name.lower() for name, _ in DemoRiotClient.seed_names}) == 8
    for display_name, riot_id in DemoRiotClient.seed_names:
        assert 2 <= len(display_name) <= 20
        parse_riot_id(riot_id)


@pytest.mark.anyio
async def test_match_has_items_spells_and_coherent_kills(api):
    account = await api.get_account_by_riot_id("La Peace", "CHILL")
    ids = await play_games(api, account.puuid, 3)
    for match_id in ids:
        info = (await api.get_match(match_id))["info"]
        remake = info["gameDuration"] < 300
        for participant in info["participants"]:
            items = [participant[f"item{i}"] for i in range(7)]
            assert all(isinstance(item, int) and item >= 0 for item in items)
            assert items[6] in (3340, 3363, 3364)  # bibelot
            filled = [item for item in items[:6] if item]
            assert len(filled) == len(set(filled))  # pas de doublon d'objet
            assert (1 <= len(filled) <= 2) if remake else (3 <= len(filled) <= 6)
            spells = {participant["summoner1Id"], participant["summoner2Id"]}
            assert 4 in spells and len(spells) == 2
            second = next(iter(spells - {4}))
            expected = {"TOP": {12, 14}, "JUNGLE": {11}, "MIDDLE": {14, 12}, "BOTTOM": {7, 3}, "UTILITY": {14, 3, 7}}
            assert second in expected[participant["teamPosition"]]
            assert 1 <= participant["champLevel"] <= 18
        # Cohérence par équipe : K + A d'un joueur ≤ kills de son équipe (KP ≤ 100 %)
        for team in info["teams"]:
            members = [p for p in info["participants"] if p["teamId"] == team["teamId"]]
            team_kills = sum(p["kills"] for p in members)
            assert team["objectives"]["champion"]["kills"] == team_kills
            for member in members:
                assert member["kills"] + member["assists"] <= team_kills


DETAIL_KEYS = (
    "doubleKills", "tripleKills", "quadraKills", "pentaKills", "largestMultiKill", "largestKillingSpree",
    "turretKills", "inhibitorKills", "dragonKills", "baronKills", "objectivesStolen", "totalDamageTaken",
    "damageSelfMitigated", "totalHeal", "totalHealsOnTeammates", "timeCCingOthers", "totalTimeSpentDead",
    "wardsPlaced", "wardsKilled", "visionWardsBoughtInGame",
)


@pytest.mark.anyio
async def test_match_has_profile_details(api):
    """Champs de détail Match-V5 (multikills, objectifs, balises, first blood, abandon) cohérents."""
    account = await api.get_account_by_riot_id("La Peace", "CHILL")
    ids = await play_games(api, account.puuid, 6)
    for match_id in ids:
        info = (await api.get_match(match_id))["info"]
        remake = info["gameDuration"] < 300
        parts = info["participants"]
        for participant in parts:
            for key in DETAIL_KEYS:
                assert isinstance(participant[key], int) and participant[key] >= 0, key
            assert isinstance(participant["firstBloodKill"], bool)
            assert isinstance(participant["gameEndedInSurrender"], bool)
            multis = sum(participant[k] * n for k, n in (("doubleKills", 2), ("tripleKills", 3), ("quadraKills", 4), ("pentaKills", 5)))
            assert multis <= participant["kills"]
            assert participant["largestMultiKill"] <= max(1, participant["kills"])
            if remake:
                assert participant["firstBloodKill"] is False and participant["gameEndedInSurrender"] is False
        # Un seul first blood (parmi les joueurs ayant tué), abandon identique pour les 10 joueurs
        if not remake and any(p["kills"] > 0 for p in parts):
            first_bloods = [p for p in parts if p["firstBloodKill"]]
            assert len(first_bloods) == 1 and first_bloods[0]["kills"] > 0
        assert len({p["gameEndedInSurrender"] for p in parts}) == 1
