"""Tests du tirage des duos (`app.services.draw`) : paires pures + application en base."""

from __future__ import annotations

import random
from collections import Counter

import pytest
from sqlmodel import Session, select

from app.db.models import Challenge, ChallengeStatus, Player, Team
from app.events import bus
from app.services.draw import TEAM_PALETTE, DrawResult, draw_pairs, perform_draw

# ---------------------------------------------------------------------------
# draw_pairs
# ---------------------------------------------------------------------------


class TestDrawPairs:
    def test_even_count_uses_every_id_once(self):
        ids = [11, 22, 33, 44, 55, 66, 77, 88]
        pairs = draw_pairs(ids, random.Random(1))
        assert len(pairs) == 4
        flat = [pid for pair in pairs for pid in pair]
        assert sorted(flat) == sorted(ids)
        assert len(set(flat)) == len(ids)

    def test_two_players(self):
        assert draw_pairs([1, 2], random.Random(0)) in ([(1, 2)], [(2, 1)])

    @pytest.mark.parametrize("ids", [[], [1], [1, 2, 3], [1, 2, 3, 4, 5, 6, 7]])
    def test_odd_or_empty_raises(self, ids: list[int]):
        with pytest.raises(ValueError, match="nombre pair"):
            draw_pairs(ids, random.Random(0))

    def test_deterministic_with_seeded_rng(self):
        ids = list(range(1, 9))
        assert draw_pairs(ids, random.Random(7)) == draw_pairs(ids, random.Random(7))

    def test_different_seeds_differ(self):
        ids = list(range(1, 9))
        results = {tuple(draw_pairs(ids, random.Random(seed))) for seed in range(20)}
        assert len(results) > 1

    def test_input_not_mutated(self):
        ids = [1, 2, 3, 4]
        draw_pairs(ids, random.Random(3))
        assert ids == [1, 2, 3, 4]

    def test_default_rng(self):
        pairs = draw_pairs([1, 2, 3, 4])
        assert sorted(pid for pair in pairs for pid in pair) == [1, 2, 3, 4]

    def test_distribution_is_roughly_uniform(self):
        # 4 joueurs → 3 appariements possibles ; chacun doit sortir ~1/3 du temps
        rng = random.Random(123)
        counter: Counter[frozenset] = Counter()
        draws = 3000
        for _ in range(draws):
            pairs = draw_pairs([1, 2, 3, 4], rng)
            counter[frozenset(frozenset(pair) for pair in pairs)] += 1
        assert len(counter) == 3
        for count in counter.values():
            assert 0.25 * draws < count < 0.42 * draws

    def test_positions_are_uniform(self):
        # Chaque joueur doit apparaître en première position environ 1/8 du temps
        rng = random.Random(99)
        ids = list(range(1, 9))
        first = Counter(draw_pairs(ids, rng)[0][0] for _ in range(4000))
        assert set(first) == set(ids)
        for count in first.values():
            assert 0.08 * 4000 < count < 0.17 * 4000


# ---------------------------------------------------------------------------
# perform_draw
# ---------------------------------------------------------------------------


def setup_challenge(
    session: Session,
    *,
    count: int = 8,
    status: ChallengeStatus = ChallengeStatus.REGISTRATION,
    unlinked: set[int] | None = None,
    inactive: set[int] | None = None,
) -> tuple[Challenge, list[Player]]:
    """Crée le challenge et `count` joueurs (index 1..count) ; `unlinked`/`inactive` par index."""
    unlinked = unlinked or set()
    inactive = inactive or set()
    challenge = Challenge(status=status)
    session.add(challenge)
    players = []
    for i in range(1, count + 1):
        player = Player(
            display_name=f"Joueur{i}",
            game_name=f"Joueur{i}",
            tag_line="EUW",
            puuid=None if i in unlinked else f"puuid-{i}",
            active=i not in inactive,
        )
        session.add(player)
        players.append(player)
    session.commit()
    for player in players:
        session.refresh(player)
    session.refresh(challenge)
    return challenge, players


class TestPerformDraw:
    def test_creates_teams_from_palette(self, session: Session):
        challenge, players = setup_challenge(session)
        result = perform_draw(session, rng=random.Random(42))

        assert isinstance(result, DrawResult)
        teams = session.exec(select(Team).order_by(Team.slot)).all()  # type: ignore[arg-type]
        assert len(teams) == 4
        assert [t.name for t in teams] == [name for name, _ in TEAM_PALETTE[:4]]
        assert [t.color for t in teams] == [color for _, color in TEAM_PALETTE[:4]]
        assert [t.slot for t in teams] == [1, 2, 3, 4]

        session.refresh(challenge)
        assert challenge.status == ChallengeStatus.DRAWN

        # Chaque joueur est dans exactement un duo, chaque duo a deux joueurs
        for player in players:
            session.refresh(player)
        by_team = Counter(p.team_id for p in players)
        assert set(by_team) == {t.id for t in teams}
        assert all(n == 2 for n in by_team.values())

    def test_result_matches_database(self, session: Session):
        _, players = setup_challenge(session)
        result = perform_draw(session, rng=random.Random(42))
        ids = {p.id for p in players}

        assert sorted(result.order) == sorted(ids)
        assert len(result.teams) == 4
        for index, team in enumerate(result.teams):
            assert set(team) == {"id", "name", "color", "slot", "player_ids"}
            assert team["slot"] == index + 1
            assert team["name"] == TEAM_PALETTE[index][0]
            assert team["color"] == TEAM_PALETTE[index][1]
            # paire i = order[2i], order[2i+1]
            assert team["player_ids"] == [result.order[2 * index], result.order[2 * index + 1]]
            db_team = session.get(Team, team["id"])
            assert db_team is not None and db_team.slot == team["slot"]
            for player_id in team["player_ids"]:
                player = session.get(Player, player_id)
                assert player is not None
                session.refresh(player)
                assert player.team_id == team["id"]

        d = result.to_dict()
        assert d["order"] == result.order
        assert d["teams"] == result.teams

    def test_deterministic_with_seed(self, session: Session):
        _, players = setup_challenge(session)
        expected = draw_pairs([p.id for p in players], random.Random(5))
        result = perform_draw(session, rng=random.Random(5))
        assert [tuple(t["player_ids"]) for t in result.teams] == expected

    def test_publishes_draw_done(self, session: Session):
        setup_challenge(session)
        result = perform_draw(session, rng=random.Random(1))
        events = bus.recent(types={"draw_done"})
        assert len(events) == 1
        assert events[0]["data"]["order"] == result.order
        assert events[0]["data"]["teams"] == result.teams

    def test_redraw_replaces_teams(self, session: Session):
        challenge, players = setup_challenge(session)
        perform_draw(session, rng=random.Random(1))
        first = {p.id: p.team_id for p in players}

        # Deuxième tirage depuis le statut `drawn`
        session.refresh(challenge)
        assert challenge.status == ChallengeStatus.DRAWN
        result = perform_draw(session, rng=random.Random(2))

        teams = session.exec(select(Team)).all()
        assert len(teams) == 4
        team_ids = {t.id for t in teams}
        assert {t["id"] for t in result.teams} == team_ids
        for player in players:
            session.refresh(player)
            assert player.team_id in team_ids
        # Les paires ont changé (graines différentes)
        second = {p.id: p.team_id for p in players}
        assert first != second

    def test_unlinked_player_raises(self, session: Session):
        setup_challenge(session, unlinked={3, 5})
        with pytest.raises(ValueError) as exc:
            perform_draw(session, rng=random.Random(0))
        assert str(exc.value) == "Comptes non liés : Joueur3, Joueur5"
        assert session.exec(select(Team)).all() == []

    def test_inactive_unlinked_player_is_ignored(self, session: Session):
        # 9 joueurs dont un inactif et non lié → 8 joueurs tirés
        _, players = setup_challenge(session, count=9, unlinked={9}, inactive={9})
        result = perform_draw(session, rng=random.Random(0))
        assert len(result.order) == 8
        assert players[8].id not in result.order
        session.refresh(players[8])
        assert players[8].team_id is None

    def test_odd_player_count_raises(self, session: Session):
        setup_challenge(session, count=7)
        with pytest.raises(ValueError, match="nombre pair"):
            perform_draw(session, rng=random.Random(0))

    def test_no_players_raises(self, session: Session):
        setup_challenge(session, count=0)
        with pytest.raises(ValueError, match="nombre pair"):
            perform_draw(session, rng=random.Random(0))

    @pytest.mark.parametrize("status", [ChallengeStatus.RUNNING, ChallengeStatus.FINISHED])
    def test_started_challenge_raises(self, session: Session, status: ChallengeStatus):
        setup_challenge(session, status=status)
        with pytest.raises(ValueError, match="déjà démarré"):
            perform_draw(session, rng=random.Random(0))
        assert session.exec(select(Team)).all() == []

    def test_missing_challenge_raises(self, session: Session):
        with pytest.raises(ValueError):
            perform_draw(session, rng=random.Random(0))

    def test_more_teams_than_palette(self, session: Session):
        setup_challenge(session, count=18)
        result = perform_draw(session, rng=random.Random(0))
        assert len(result.teams) == 9
        assert result.teams[7]["name"] == TEAM_PALETTE[7][0]
        assert result.teams[8]["name"] == "Duo 9"
        assert result.teams[8]["color"] == TEAM_PALETTE[0][1]
