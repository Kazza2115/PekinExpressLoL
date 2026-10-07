"""Moteur SQLAlchemy / sessions SQLModel.

SQLite par défaut ; le code reste portable (DATABASE_URL). Les datetimes sont
stockés naïfs par SQLite : `as_utc()` les ré-attache en UTC à la lecture.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event, literal
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import PROJECT_ROOT, get_settings

log = logging.getLogger("pekin.db")

_engine: Engine | None = None


def _sqlite_path(url: str) -> Path | None:
    if url.startswith("sqlite:///") and ":memory:" not in url:
        raw = url[len("sqlite:///") :]
        path = Path(raw)
        return path if path.is_absolute() else PROJECT_ROOT / path
    return None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
            path = _sqlite_path(url)
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                url = f"sqlite:///{path}"
        _engine = create_engine(url, connect_args=connect_args)
        if url.startswith("sqlite"):
            # Mode WAL : lectures concurrentes pendant que le poller écrit
            @event.listens_for(_engine, "connect")
            def _set_sqlite_pragma(dbapi_connection, _record):  # noqa: ANN001
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
    return _engine


def set_engine(engine: Engine | None) -> None:
    """Remplace le moteur (tests : SQLite en mémoire)."""
    global _engine
    _engine = engine


def init_db() -> None:
    """Crée les tables manquantes puis ajoute les colonnes absentes des tables existantes."""
    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    apply_light_migrations(engine)


def _default_literal(column, dialect) -> str | None:  # noqa: ANN001
    """Défaut scalaire du modèle rendu en littéral SQL (None si absent ou calculé)."""
    default = column.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    try:
        compiled = literal(default.arg, column.type).compile(dialect=dialect, compile_kwargs={"literal_binds": True})
    except Exception:  # noqa: BLE001 — type exotique : on laisse NULL
        return None
    return str(compiled)


def apply_light_migrations(engine: Engine) -> list[str]:
    """Migration légère SQLite : `ALTER TABLE … ADD COLUMN` pour chaque colonne du modèle
    absente d'une table existante (ex. `game_end`, `team_side` ajoutées après la création
    de la base). Les lignes existantes prennent le défaut scalaire du modèle, sinon NULL.
    Renvoie la liste des colonnes ajoutées (« table.colonne »).
    """
    if engine.dialect.name != "sqlite":
        return []
    added: list[str] = []
    with engine.begin() as connection:
        for table in SQLModel.metadata.sorted_tables:
            rows = connection.exec_driver_sql(f'PRAGMA table_info("{table.name}")').fetchall()
            existing = {row[1] for row in rows}
            if not existing:
                continue  # table absente : `create_all` vient de la créer, rien à migrer
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(engine.dialect)}'
                default_sql = _default_literal(column, engine.dialect)
                if default_sql is not None:
                    ddl += f" DEFAULT {default_sql}"
                    if not column.nullable:
                        # SQLite n'accepte NOT NULL à l'ajout d'une colonne qu'avec un DEFAULT
                        ddl += " NOT NULL"
                connection.exec_driver_sql(ddl)
                added.append(f"{table.name}.{column.name}")
                log.info("Migration : colonne %s.%s ajoutée (%s)", table.name, column.name, ddl)
    return added


def get_session() -> Iterator[Session]:
    """Dépendance FastAPI."""
    with Session(get_engine()) as session:
        yield session


def session_scope() -> Session:
    """Usage hors FastAPI : `with session_scope() as session:`."""
    return Session(get_engine())


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite perd le fuseau : on ré-attache UTC aux datetimes naïfs."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
