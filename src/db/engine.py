"""Step 5 — SQLAlchemy engine + schema bootstrap.

``make_engine`` reads connection settings from env (or accepts an explicit URL,
useful for tests with ``sqlite:///:memory:``). ``init_db`` creates the ORM
tables and — on Postgres only — applies ``schema.sql`` for the pgvector and
pg_trgm extensions / indexes.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base
from src.utils.logging import get_logger

log = get_logger(__name__)

SCHEMA_SQL_PATH = Path(__file__).resolve().parent / "schema.sql"


def database_url() -> str:
    """Return the configured Postgres URL from the ``POSTGRES_*`` env vars."""
    user = os.environ.get("POSTGRES_USER", "lg")
    password = os.environ.get("POSTGRES_PASSWORD", "lg_password")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = os.environ.get("POSTGRES_DB", "lg_bom")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"


def make_engine(url: str | None = None) -> Engine:
    """Create a SQLAlchemy Engine for the given URL (or env-derived default).

    Args:
        url: Optional explicit SQLAlchemy URL.

    Returns:
        A configured :class:`sqlalchemy.Engine`.
    """
    return create_engine(url or database_url())


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a SQLAlchemy ``sessionmaker`` bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all tables, plus Postgres-only extensions and indexes.

    Args:
        engine: A bound SQLAlchemy Engine.
    """
    Base.metadata.create_all(engine)
    if engine.dialect.name != "postgresql":
        log.info("db.init.skipped_postgres_extensions", dialect=engine.dialect.name)
        return

    statements = [
        stmt.strip()
        for stmt in SCHEMA_SQL_PATH.read_text(encoding="utf-8").split(";")
        if stmt.strip() and not stmt.strip().startswith("--")
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    log.info("db.init.done", dialect=engine.dialect.name)
