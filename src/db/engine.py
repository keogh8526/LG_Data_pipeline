"""D-012 — SQLAlchemy engine + dev_part_master schema bootstrap.

``make_engine`` reads connection settings from env (or accepts an explicit URL,
useful for tests with ``sqlite:///:memory:``). ``init_db`` creates the ORM
tables and — on Postgres only — applies ``schema_dev_part_master.sql`` for
pgvector / pg_trgm extensions and indexes.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
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
    """Create a SQLAlchemy Engine for the given URL (or env-derived default).

    SQLite는 기본적으로 FK 제약을 무시 — ``PRAGMA foreign_keys=ON``으로 활성화해야
    ON DELETE CASCADE가 동작 (rollback_file 의존).
    """
    engine = create_engine(url or database_url())

    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def _enable_sqlite_fk(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a SQLAlchemy ``sessionmaker`` bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False)


_FORM_REGISTRY_SEED = [
    ("changing_parts_list_91", "변경부품 list 91컬럼"),
    ("changing_parts_list_95", "변경부품 list 95컬럼"),
    ("changing_parts_list_96", "변경부품 list 96컬럼"),
    ("changing_parts_list_97", "변경부품 list 97컬럼"),
    ("new_parts_list_75", "신규부품리스트 75컬럼"),
    ("base_master_24", "구버전 24컬럼"),
    ("uae_dev_list", "UAE 신규개발리스트"),
    ("bom_ag_grid_36", "BOM ag-grid 36컬럼"),
    ("v1_2_template_59", "v1.2 통합 마스터 (빈 템플릿)"),
]


def init_db(engine: Engine) -> None:
    """Create all tables + Postgres extensions/indexes via raw SQL.

    On Postgres: ``schema_dev_part_master.sql``이 모든 테이블 / 확장 / 인덱스 +
    form_registry seed까지 한 번에 처리.

    SQLite (단위 테스트): ORM ``create_all`` + form_registry seed 별도 INSERT
    (Vector → JSON variant, FK 활성화는 make_engine에서).
    """
    from src.db.models import FormRegistry

    if engine.dialect.name != "postgresql":
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            for form_id, description in _FORM_REGISTRY_SEED:
                conn.execute(
                    text(
                        "INSERT OR IGNORE INTO form_registry (form_id, description) "
                        "VALUES (:fid, :desc)"
                    ),
                    {"fid": form_id, "desc": description},
                )
        log.info("db.init.sqlite_only", dialect=engine.dialect.name)
        return

    sql = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    log.info("db.init.done", dialect=engine.dialect.name)
