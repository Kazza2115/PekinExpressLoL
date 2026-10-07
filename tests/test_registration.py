"""Inscription / liaison (`services.registration`) et amorçage (`services.bootstrap`)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlmodel import Session, select

from app.config import get_settings
from app.db.models import Challenge, Player, Queue, RankSnapshot
from app.events import bus
from app.riot.base import RiotError, RiotNotFound, RiotRateLimited, RiotUnauthorized, RiotUnreachable
from app.services.bootstrap import ensure_challenge, load_players_yaml
from app.services.registration import link_player, parse_riot_id, register_player


@pytest.fixture
def anyio_backend():
    return "asyncio"


class FailingApi:
    """Client Riot factice : lève `error` à la résolution du Riot ID."""

    def __init__(self, error: Exception):
        self.error = error

    async def get_account_by_riot_id(self, game_name: str, tag_line: str):
        raise self.error


def snapshots_of(session: Session, player: Player) -> list[RankSnapshot]:
    return session.exec(select(RankSnapshot).where(RankSnapshot.player_id == player.id)).all()


# --------------------------------------------------------------------------- parse_riot_id


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("La Peace#CHILL", ("La Peace", "CHILL")),
        ("  Lealicious#EUW  ", ("Lealicious", "EUW")),
        ("Nom # 123", ("Nom", "123")),
        ("Chloé#GG1", ("Chloé", "GG1")),
    ],
)
def test_parse_riot_id_valid(raw, expected):
    assert parse_riot_id(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "   ", None, "SansTag", "Ab#EUW", "A" * 17 + "#EUW", "Nom#EU", "Nom#TOOLONG", "Nom#EU-W", "Nom#TAG#EUW"],
)
def test_parse_riot_id_invalid(raw):
    with pytest.raises(ValueError) as excinfo:
        parse_riot_id(raw)
    assert str(excinfo.value)  # message en français, non vide


# --------------------------------------------------------------------------- register_player


@pytest.mark.anyio
async def test_register_player_links_account(session, demo_api):
    player = await register_player(session, demo_api, display_name="  Mike  ", riot_id="La Peace#CHILL")
    assert player.id is not None
    assert player.display_name == "Mike"
    assert player.is_linked and player.puuid.startswith("demo-")
    assert (player.game_name, player.tag_line, player.riot_id) == ("La Peace", "CHILL", "La Peace#CHILL")
    assert player.linked_at is not None and player.link_error is None
    assert player.profile_icon_id is not None and player.summoner_level is not None

    snapshots = snapshots_of(session, player)
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.queue == Queue.SOLO
    assert snapshot.tier is not None and snapshot.rank is not None
    assert snapshot.absolute_lp is not None and snapshot.absolute_lp > 0

    types = [event["type"] for event in bus.recent()]
    assert "player_linked" in types and "player_registered" in types
    linked = next(e for e in bus.recent() if e["type"] == "player_linked")
    assert linked["data"]["player_id"] == player.id and linked["data"]["tier"] == snapshot.tier


@pytest.mark.anyio
async def test_register_player_without_riot_id(session, demo_api):
    player = await register_player(session, demo_api, display_name="Tom", riot_id=None)
    assert player.id is not None and not player.is_linked
    assert player.link_error is None and player.linked_at is None
    assert snapshots_of(session, player) == []
    assert session.get(Player, player.id) is not None
    # Chaîne vide = pas de Riot ID
    other = await register_player(session, demo_api, display_name="Léa", riot_id="   ")
    assert not other.is_linked


@pytest.mark.anyio
async def test_display_name_validation(session, demo_api):
    await register_player(session, demo_api, display_name="Mike", riot_id=None)
    with pytest.raises(ValueError, match="déjà pris"):
        await register_player(session, demo_api, display_name="mIKE", riot_id=None)
    with pytest.raises(ValueError):
        await register_player(session, demo_api, display_name="M", riot_id=None)
    with pytest.raises(ValueError):
        await register_player(session, demo_api, display_name="X" * 21, riot_id=None)
    assert len(session.exec(select(Player)).all()) == 1


@pytest.mark.anyio
async def test_invalid_riot_id_creates_nothing(session, demo_api):
    with pytest.raises(ValueError):
        await register_player(session, demo_api, display_name="Mike", riot_id="PasDeTag")
    assert session.exec(select(Player)).all() == []


@pytest.mark.anyio
async def test_puuid_already_linked_to_another_player(session, demo_api):
    await register_player(session, demo_api, display_name="Mike", riot_id="La Peace#CHILL")
    with pytest.raises(ValueError, match="déjà lié à Mike"):
        await register_player(session, demo_api, display_name="Clone", riot_id="La Peace#CHILL")
    names = [p.display_name for p in session.exec(select(Player)).all()]
    assert names == ["Mike"]  # le doublon n'a pas été conservé


@pytest.mark.parametrize(
    "error, expected",
    [
        (RiotNotFound("404", status=404), "Riot ID introuvable : vérifie le pseudo et le tag."),
        (RiotUnauthorized("403", status=403), "Clé Riot invalide ou expirée (voir .env)."),
        (RiotRateLimited("429", status=429), "API Riot saturée, réessaie dans une minute."),
        (RiotUnreachable("Erreur réseau Riot sur /x : ReadTimeout"),
         "API Riot injoignable : vérifie la connexion et réessaie."),
        (RiotError("Riot 500 sur /x", status=500), "Erreur API Riot : Riot 500 sur /x"),
        (httpx.ConnectError("boom"), "Erreur API Riot : boom"),
    ],
)
@pytest.mark.anyio
async def test_riot_errors_become_link_error(session, error, expected):
    player = await register_player(session, FailingApi(error), display_name="Mike", riot_id="La Peace#CHILL")
    assert player.id is not None and session.get(Player, player.id) is not None
    assert player.link_error == expected
    assert player.puuid is None and player.linked_at is None
    assert (player.game_name, player.tag_line) == ("La Peace", "CHILL")  # saisie conservée
    assert snapshots_of(session, player) == []
    assert "player_registered" in [e["type"] for e in bus.recent()]


@pytest.mark.anyio
async def test_relink_after_error_then_fix(session, demo_api):
    player = await register_player(session, FailingApi(RiotNotFound("404")), display_name="Mike", riot_id="Faux#EUW")
    assert player.link_error
    player = await link_player(session, demo_api, player, "La Peace#CHILL")
    assert player.is_linked and player.link_error is None
    assert player.riot_id == "La Peace#CHILL"
    assert len(snapshots_of(session, player)) == 1


@pytest.mark.anyio
async def test_relink_to_other_account_resets_snapshots(session, demo_api):
    player = await register_player(session, demo_api, display_name="Mike", riot_id="La Peace#CHILL")
    first_puuid = player.puuid
    player = await link_player(session, demo_api, player, "Lealicious#EUW")
    assert player.puuid != first_puuid
    assert len(snapshots_of(session, player)) == 1  # ancien historique supprimé, nouveau snapshot de référence
    # Erreur lors d'une correction : le puuid précédent est conservé
    player = await link_player(session, FailingApi(RiotUnauthorized("403")), player, "Autre#EUW")
    assert player.puuid != first_puuid and player.is_linked
    assert player.link_error == "Clé Riot invalide ou expirée (voir .env)."
    assert player.riot_id == "Lealicious#EUW"


@pytest.mark.anyio
async def test_unranked_account_gets_reference_snapshot(session, demo_api):
    player = await register_player(session, demo_api, display_name="Nico", riot_id="unranked#EUW")
    assert player.is_linked
    snapshots = snapshots_of(session, player)
    assert len(snapshots) == 1
    assert snapshots[0].tier is None and snapshots[0].absolute_lp is None


@pytest.mark.anyio
async def test_track_flex_adds_flex_snapshot(session, demo_api, monkeypatch):
    monkeypatch.setattr(get_settings(), "track_flex", True)
    player = await register_player(session, demo_api, display_name="Mike", riot_id="La Peace#CHILL")
    queues = sorted(s.queue for s in snapshots_of(session, player))
    assert queues == sorted([Queue.SOLO, Queue.FLEX])
    flex = next(s for s in snapshots_of(session, player) if s.queue == Queue.FLEX)
    assert flex.tier is None  # le client démo ne simule pas la flex : unranked


# --------------------------------------------------------------------------- bootstrap


def test_ensure_challenge_creates_single_row(session):
    challenge = ensure_challenge(session)
    assert challenge.id is not None
    assert challenge.games_per_day == get_settings().games_per_day
    assert ensure_challenge(session).id == challenge.id
    assert len(session.exec(select(Challenge)).all()) == 1


def test_ensure_challenge_without_session(engine):
    challenge = ensure_challenge()
    assert challenge.id is not None and challenge.name  # attributs accessibles une fois détaché
    assert ensure_challenge().id == challenge.id


@pytest.mark.anyio
async def test_load_players_yaml(engine, demo_api, tmp_path: Path):
    from app import riot as riot_pkg

    riot_pkg._api = demo_api  # noqa: SLF001
    path = tmp_path / "players.yaml"
    path.write_text(
        "challenge:\n  name: \"Week-end test\"\n  games_per_day: 7\n"
        "players:\n"
        "  - display_name: Mike\n    riot_id: \"La Peace#CHILL\"\n"
        "  - display_name: Tom\n"
        "  - display_name: Mike\n    riot_id: \"Doublon#EUW\"\n"
        "  - display_name: Bad\n    riot_id: \"SansTag\"\n",
        encoding="utf-8",
    )
    await load_players_yaml(path)
    with Session(engine) as session:
        challenge = ensure_challenge(session)
        assert (challenge.name, challenge.games_per_day) == ("Week-end test", 7)
        players = session.exec(select(Player)).all()
        assert sorted(p.display_name for p in players) == ["Mike", "Tom"]
        mike = next(p for p in players if p.display_name == "Mike")
        assert mike.is_linked

    # Table non vide : rechargement sans effet
    path.write_text("challenge:\n  name: Autre\nplayers:\n  - display_name: Hugo\n", encoding="utf-8")
    await load_players_yaml(path)
    with Session(engine) as session:
        assert len(session.exec(select(Player)).all()) == 2
        assert ensure_challenge(session).name == "Week-end test"


@pytest.mark.anyio
async def test_load_players_yaml_tolerates_missing_or_empty(engine, tmp_path: Path):
    await load_players_yaml(tmp_path / "absent.yaml")
    empty = tmp_path / "empty.yaml"
    empty.write_text("challenge:\n  name: Vide\nplayers: []\n", encoding="utf-8")
    await load_players_yaml(empty)
    with Session(engine) as session:
        assert session.exec(select(Player)).all() == []
        assert ensure_challenge(session).name == "Vide"
    (tmp_path / "blank.yaml").write_text("", encoding="utf-8")
    await load_players_yaml(tmp_path / "blank.yaml")
