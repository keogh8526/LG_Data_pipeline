"""D-012 — SQLAlchemy engine + dev_part_master schema bootstrap.

``make_engine`` reads connection settings from env (or accepts an explicit URL,
useful for tests with ``sqlite:///:memory:``). ``init_db`` creates the ORM
tables and — on Postgres only — applies ``schema_dev_part_master.sql`` for
pgvector / pg_trgm extensions and indexes.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import Base
from src.utils.logging import get_logger

log = get_logger(__name__)

SCHEMA_SQL_PATH = Path(__file__).resolve().parent / "schema_dev_part_master.sql"


def database_url() -> str:
    """Return the configured Postgres URL from the ``POSTGRES_*`` env vars."""
    user = os.environ.get("POSTGRES_USER", "lg")
    password = os.environ.get("POSTGRES_PASSWORD", "lg_password")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    database = os.environ.get("POSTGRES_DB", "lg_bom")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{database}"


def make_engine(url: str | None = None) -> Engine:
    """Create a SQLAlchemy Engine for the given URL (or env-derived default)."""
    return create_engine(url or database_url())


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a SQLAlchemy ``sessionmaker`` bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all tables + Postgres extensions/indexes via raw SQL.

    On Postgres: ``schema_dev_part_master.sql``이 모든 테이블 / 확장 / 인덱스를
    함께 생성한다 (vector / pg_trgm 확장 의존). Base.metadata.create_all은
    skip 해서 ``vector(1024)`` 타입 충돌을 회피.

    SQLite (단위 테스트): ORM ``create_all``만 수행 (Vector → JSON variant).
    """
    if engine.dialect.name != "postgresql":
        Base.metadata.create_all(engine)
        log.info("db.init.sqlite_only", dialect=engine.dialect.name)
        return

    sql = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    # 단순 split — 본 파일은 PL/pgSQL 함수 없음, ';' 기준 안전.
    statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    log.info("db.init.done", dialect=engine.dialect.name)
