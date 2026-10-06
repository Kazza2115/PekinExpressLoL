"""Moteur SQLAlchemy / sessions SQLModel.

SQLite par défaut ; le code reste portable (DATABASE_URL). Les datetimes sont
stockés naïfs par SQLite : `as_utc()` les ré-attache en UTC à la lecture.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import PROJECT_ROOT, get_settings

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
    SQLModel.metadata.create_all(get_engine())


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
