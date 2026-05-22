"""Step 5 — parquet -> PostgreSQL loader and CLI.

Loads are idempotent: rows are upserted via ``ON CONFLICT`` so re-running a
load produces an identical table state.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import typer
from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine

from src.load.sql_schema import Base, ChangeEvent, Model, Part, make_engine
from src.utils.logging import get_logger

log = get_logger(__name__)


def init_db(engine: Engine) -> None:
    """Create the pg_trgm extension and all tables.

    Args:
        engine: Target database engine.
    """
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
    Base.metadata.create_all(engine)
    log.info("loader.init_db", tables=len(Base.metadata.tables))


def _upsert(engine: Engine, model: type[Base], rows: list[dict[str, object]]) -> int:
    """Upsert rows into a table keyed on its primary key.

    Args:
        engine: Target engine.
        model: ORM model class.
        rows: Row dicts to upsert.

    Returns:
        Number of rows submitted.
    """
    if not rows:
        return 0
    pk_cols = [c.name for c in inspect(model).primary_key]
    update_cols = {
        c.name: c for c in inspect(model).columns if c.name not in pk_cols
    }
    with engine.begin() as conn:
        stmt = insert(model).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=pk_cols,
            set_={name: stmt.excluded[name] for name in update_cols},
        )
        conn.execute(stmt)
    return len(rows)


def load_parts(engine: Engine, df: pd.DataFrame) -> int:
    """Upsert part rows.

    Args:
        engine: Target engine.
        df: DataFrame with at least a ``part_no`` column.

    Returns:
        Number of rows upserted.
    """
    cols = {c.name for c in inspect(Part).columns}
    rows = [
        {k: v for k, v in r.items() if k in cols}
        for r in df.to_dict(orient="records")
    ]
    return _upsert(engine, Part, rows)


def load_models(engine: Engine, df: pd.DataFrame) -> int:
    """Upsert model rows.

    Args:
        engine: Target engine.
        df: DataFrame with at least a ``model_code`` column.

    Returns:
        Number of rows upserted.
    """
    cols = {c.name for c in inspect(Model).columns}
    rows = [
        {k: v for k, v in r.items() if k in cols}
        for r in df.to_dict(orient="records")
    ]
    return _upsert(engine, Model, rows)


def verify(engine: Engine) -> dict[str, object]:
    """Run integrity checks and return a report.

    Args:
        engine: Target engine.

    Returns:
        A report dict with table counts and FK-violation counts.
    """
    report: dict[str, object] = {}
    with engine.connect() as conn:
        for table in Base.metadata.tables:
            count = conn.execute(
                text(f"SELECT count(*) FROM {table}")
            ).scalar_one()
            report[table] = count
        orphans = conn.execute(
            text(
                "SELECT count(*) FROM change_events e "
                "LEFT JOIN parts p ON e.new_part_no = p.part_no "
                "WHERE p.part_no IS NULL"
            )
        ).scalar_one()
    report["orphan_change_events"] = orphans
    log.info("loader.verify", **{k: v for k, v in report.items()})
    return report


app = typer.Typer(help="PostgreSQL loader.")


@app.command("init-db")
def cli_init_db() -> None:
    """Create the schema (extensions + tables)."""
    init_db(make_engine())
    typer.echo("Schema created.")


@app.command("load")
def cli_load(
    parts: Path = typer.Option(None, help="parts parquet path."),
    models: Path = typer.Option(None, help="models parquet path."),
) -> None:
    """Load entity parquet files into the database."""
    engine = make_engine()
    if parts is not None:
        n = load_parts(engine, pd.read_parquet(parts))
        typer.echo(f"parts: {n} rows upserted")
    if models is not None:
        n = load_models(engine, pd.read_parquet(models))
        typer.echo(f"models: {n} rows upserted")


@app.command("verify")
def cli_verify() -> None:
    """Run integrity checks and print the report."""
    report = verify(make_engine())
    for key, value in report.items():
        typer.echo(f"  {key}: {value}")


if __name__ == "__main__":
    app()
