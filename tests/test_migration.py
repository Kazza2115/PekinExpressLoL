"""Migration légère de `init_db()` : colonnes ajoutées aux tables d'une base plus ancienne."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlmodel import Session, select

from app.db import session as db_session
from app.db.models import Match, MatchParticipant, Player, game_end_of
from app.db.session import apply_light_migrations, init_db

# Schéma « d'hier » : `match` / `matchparticipant` sans `game_end`, `team_side`, `items`, `spells`,
# `champ_level`, `kill_participation` ; `player` sans `active` (NOT NULL avec défaut) ni `link_error`
OLD_MATCH = """
CREATE TABLE match (
    match_id VARCHAR NOT NULL PRIMARY KEY, queue_id INTEGER NOT NULL, game_start DATETIME NOT NULL,
    game_duration INTEGER NOT NULL, raw_json VARCHAR NOT NULL, fetched_at DATETIME NOT NULL
)
"""
OLD_PARTICIPANT = """
CREATE TABLE matchparticipant (
    id INTEGER NOT NULL PRIMARY KEY, match_id VARCHAR NOT NULL, player_id INTEGER NOT NULL,
    queue VARCHAR(4) NOT NULL, game_start DATETIME NOT NULL, game_duration INTEGER NOT NULL,
    is_remake BOOLEAN NOT NULL, champion_name VARCHAR NOT NULL, champion_id INTEGER, position VARCHAR,
    win BOOLEAN NOT NULL, kills INTEGER NOT NULL, deaths INTEGER NOT NULL, assists INTEGER NOT NULL,
    cs INTEGER NOT NULL, gold INTEGER NOT NULL, damage_to_champions INTEGER NOT NULL,
    vision_score INTEGER NOT NULL, lp_change INTEGER
)
"""
OLD_PLAYER = """
CREATE TABLE player (
    id INTEGER NOT NULL PRIMARY KEY, display_name VARCHAR NOT NULL, game_name VARCHAR, tag_line VARCHAR,
    puuid VARCHAR UNIQUE, summoner_id VARCHAR, profile_icon_id INTEGER, summoner_level INTEGER,
    team_id INTEGER, linked_at DATETIME, created_at DATETIME NOT NULL
)
"""


def _columns(engine, table: str) -> set[str]:
    with engine.connect() as connection:
        return {row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")')}


def test_init_db_adds_missing_columns(tmp_path):
    path = tmp_path / "old.db"
    start = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    old = create_engine(f"sqlite:///{path}")
    with old.begin() as connection:
        connection.exec_driver_sql(OLD_MATCH)
        connection.exec_driver_sql(OLD_PARTICIPANT)
        connection.exec_driver_sql(OLD_PLAYER)
        connection.exec_driver_sql(
            "INSERT INTO match VALUES ('EUW1_1', 420, '2026-10-10 12:00:00', 1800, '{}', '2026-10-10 12:40:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO matchparticipant (match_id, player_id, queue, game_start, game_duration, is_remake, "
            "champion_name, win, kills, deaths, assists, cs, gold, damage_to_champions, vision_score) "
            "VALUES ('EUW1_1', 1, 'SOLO', '2026-10-10 12:00:00', 1800, 0, 'Ahri', 1, 5, 2, 7, 180, 12000, 15000, 20)"
        )
        connection.exec_driver_sql(
            "INSERT INTO player (id, display_name, puuid, created_at) VALUES (1, 'Mike', 'p-mike', '2026-10-01 10:00:00')"
        )
    old.dispose()

    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    db_session.set_engine(engine)
    try:
        init_db()
        assert {"game_end", "team_side", "items", "spells", "champ_level", "kill_participation"} <= _columns(
            engine, "matchparticipant"
        )
        assert "game_end" in _columns(engine, "match")
        assert {"active", "link_error"} <= _columns(engine, "player")
        # Les autres tables ont été créées normalement
        assert _columns(engine, "challenge") and _columns(engine, "team") and _columns(engine, "ranksnapshot")

        with Session(engine) as session:
            row = session.exec(select(MatchParticipant)).one()
            assert row.champion_name == "Ahri" and row.kills == 5
            assert row.game_end is None and row.team_side is None
            assert row.items is None and row.spells is None
            assert row.champ_level is None and row.kill_participation is None
            assert game_end_of(row) == start + timedelta(seconds=1800)  # repli : début + durée
            match = session.exec(select(Match)).one()
            assert match.game_end is None and game_end_of(match) == start + timedelta(seconds=1800)
            player = session.exec(select(Player)).one()
            assert player.active is True  # défaut scalaire du modèle appliqué aux lignes existantes
            assert player.link_error is None
            # Écriture avec les nouvelles colonnes
            row.team_side = 100
            row.items = "[3031,0,0,0,0,0,3340]"
            session.add(row)
            session.commit()
            session.refresh(row)
            assert row.team_side == 100

        # Idempotent : plus rien à ajouter au second passage
        assert apply_light_migrations(engine) == []
    finally:
        db_session.set_engine(None)
        engine.dispose()


def test_fresh_database_needs_no_migration(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}", connect_args={"check_same_thread": False})
    db_session.set_engine(engine)
    try:
        init_db()
        assert apply_light_migrations(engine) == []
    finally:
        db_session.set_engine(None)
        engine.dispose()


def test_old_database_gets_detail_columns_and_backfill(tmp_path):
    """Base antérieure aux colonnes de détail : ajout par `init_db`, puis rattrapage depuis `Match.raw_json`."""
    import json

    from app.services.bootstrap import backfill_match_details
    from app.services.poller import DETAIL_COLUMNS

    raw = {
        "metadata": {"matchId": "EUW1_2"},
        "info": {
            "participants": [
                {
                    "puuid": "p-mike", "championName": "Ahri", "championId": 103, "teamId": 100,
                    "doubleKills": 2, "pentaKills": 1, "largestMultiKill": 5, "wardsPlaced": 11,
                    "firstBloodKill": True, "gameEndedInSurrender": False,
                    "totalDamageDealtToChampions": 30000, "goldEarned": 12000,
                },
                {"puuid": "x", "championName": "Garen", "teamId": 100, "totalDamageDealtToChampions": 10000, "goldEarned": 8000},
                # Compte re-lié depuis : retrouvé par champion et côté
                {"puuid": "old-puuid", "championName": "Lux", "championId": 99, "teamId": 200, "largestMultiKill": 1},
            ]
        },
    }
    path = tmp_path / "old.db"
    old = create_engine(f"sqlite:///{path}")
    with old.begin() as connection:
        connection.exec_driver_sql(OLD_MATCH)
        connection.exec_driver_sql(OLD_PARTICIPANT)
        connection.exec_driver_sql(OLD_PLAYER)
        for match_id, raw_json in (("EUW1_2", json.dumps(raw)), ("EUW1_3", "{}"), ("EUW1_4", "pas du json")):
            connection.exec_driver_sql(
                "INSERT INTO match VALUES (?, 420, '2026-10-10 12:00:00', 1800, ?, '2026-10-10 12:40:00')",
                (match_id, raw_json),
            )
        for player_id, match_id, champion in ((1, "EUW1_2", "Ahri"), (2, "EUW1_2", "Lux"), (1, "EUW1_3", "Ahri"), (1, "EUW1_4", "Ahri")):
            connection.exec_driver_sql(
                "INSERT INTO matchparticipant (match_id, player_id, queue, game_start, game_duration, is_remake, "
                "champion_name, win, kills, deaths, assists, cs, gold, damage_to_champions, vision_score) "
                "VALUES (?, ?, 'SOLO', '2026-10-10 12:00:00', 1800, 0, ?, 1, 5, 2, 7, 180, 12000, 15000, 20)",
                (match_id, player_id, champion),
            )
        connection.exec_driver_sql(
            "INSERT INTO player (id, display_name, puuid, created_at) VALUES (1, 'Mike', 'p-mike', '2026-10-01 10:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO player (id, display_name, puuid, created_at) VALUES (2, 'Léa', 'p-lea', '2026-10-01 10:00:00')"
        )
    old.dispose()

    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    db_session.set_engine(engine)
    try:
        init_db()
        assert set(DETAIL_COLUMNS) <= _columns(engine, "matchparticipant")
        with Session(engine) as session:
            assert all(row.largest_multi_kill is None for row in session.exec(select(MatchParticipant)).all())
            assert backfill_match_details(session) == 2  # EUW1_2 (Mike et Léa) ; JSON vide / illisible ignorés
        with Session(engine) as session:
            rows = {(r.player_id, r.match_id): r for r in session.exec(select(MatchParticipant)).all()}
            mike = rows[(1, "EUW1_2")]
            assert (mike.double_kills, mike.penta_kills, mike.largest_multi_kill, mike.wards_placed) == (2, 1, 5, 11)
            assert mike.first_blood_kill is True and mike.surrendered is False
            assert mike.triple_kills is None  # clé absente du JSON
            assert mike.damage_share == 75.0 and mike.gold_share == 60.0
            assert rows[(2, "EUW1_2")].largest_multi_kill == 1  # retrouvé par champion
            assert rows[(1, "EUW1_3")].largest_multi_kill is None
            assert rows[(1, "EUW1_4")].largest_multi_kill is None
            # Second passage : plus rien à rattraper
            assert backfill_match_details(session) == 0
    finally:
        db_session.set_engine(None)
        engine.dispose()
