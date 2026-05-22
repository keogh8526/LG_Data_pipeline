"""Alembic migration environment.

The target metadata is wired to the SQLAlchemy schema so that, once a live
database is available, ``alembic revision --autogenerate`` produces the initial
migration. No version files are committed yet — see DECISIONS D-002.
"""

from __future__ import annotations

from alembic import context

from src.load.sql_schema import Base, make_engine

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in offline (SQL-emitting) mode."""
    context.configure(
        url=str(make_engine().url),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database connection."""
    engine = make_engine()
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
