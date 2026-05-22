"""Step 5 — PostgreSQL schema (SQLAlchemy 2.0 declarative).

Seven core tables form the relational single source of truth. Every table
carries provenance columns (``source_file``, ``form_version``) and timestamps.
"""

from __future__ import annotations

import os
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class _Provenance:
    """Mixin: timestamps + source provenance columns."""

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    source_file: Mapped[str | None] = mapped_column(String(512))
    form_version: Mapped[str | None] = mapped_column(String(32))


class Part(_Provenance, Base):
    """A normalized part."""

    __tablename__ = "parts"

    part_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    part_name: Mapped[str | None] = mapped_column(String(256))
    bom_level: Mapped[int | None] = mapped_column(Integer)
    part_type: Mapped[str | None] = mapped_column(String(64))
    invalid: Mapped[bool] = mapped_column(default=False)

    __table_args__ = (Index("ix_parts_form_version", "form_version"),)


class Model(_Provenance, Base):
    """A normalized appliance model."""

    __tablename__ = "models"

    model_code: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_name: Mapped[str | None] = mapped_column(String(64))
    grade: Mapped[str | None] = mapped_column(String(32))
    region: Mapped[str | None] = mapped_column(String(16))
    buyer_code: Mapped[str | None] = mapped_column(String(16))
    production_date: Mapped[str | None] = mapped_column(String(32))


class BomEdge(_Provenance, Base):
    """A parent->child BOM relationship."""

    __tablename__ = "bom_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_code: Mapped[str] = mapped_column(
        ForeignKey("models.model_code", ondelete="CASCADE")
    )
    parent_part_no: Mapped[str] = mapped_column(
        ForeignKey("parts.part_no", ondelete="CASCADE")
    )
    child_part_no: Mapped[str] = mapped_column(
        ForeignKey("parts.part_no", ondelete="CASCADE")
    )
    qty: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        Index("ix_bom_model_parent", "model_code", "parent_part_no"),
    )


class ChangeEvent(_Provenance, Base):
    """A part-change event — the central fact table."""

    __tablename__ = "change_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    base_part_no: Mapped[str | None] = mapped_column(
        ForeignKey("parts.part_no", ondelete="SET NULL")
    )
    new_part_no: Mapped[str] = mapped_column(
        ForeignKey("parts.part_no", ondelete="CASCADE")
    )
    model_code: Mapped[str | None] = mapped_column(
        ForeignKey("models.model_code", ondelete="SET NULL")
    )
    change_type: Mapped[str] = mapped_column(String(32))
    change_point: Mapped[str | None] = mapped_column(Text)
    change_reason: Mapped[str | None] = mapped_column(Text)
    qty: Mapped[float | None] = mapped_column(Float)
    raw_data: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_events_form_version", "form_version"),
        Index("ix_events_model", "model_code"),
        # Trigram index on free-text change_point for fuzzy search (pg_trgm).
        Index(
            "ix_events_change_point_trgm",
            "change_point",
            postgresql_using="gin",
            postgresql_ops={"change_point": "gin_trgm_ops"},
        ),
    )


class PartGrade(_Provenance, Base):
    """A part-grade review record."""

    __tablename__ = "part_grades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    part_no: Mapped[str] = mapped_column(
        ForeignKey("parts.part_no", ondelete="CASCADE")
    )
    grade: Mapped[str | None] = mapped_column(String(32))
    decision: Mapped[str | None] = mapped_column(Text)


class TestPlan(_Provenance, Base):
    """A test plan attached to a change event."""

    __tablename__ = "test_plans"

    plan_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("change_events.event_id", ondelete="CASCADE")
    )
    test_item: Mapped[str | None] = mapped_column(Text)
    responsible: Mapped[str | None] = mapped_column(String(128))


class HsmsRecord(_Provenance, Base):
    """A hazardous-substance (HSMS) record."""

    __tablename__ = "hsms_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    part_no: Mapped[str] = mapped_column(
        ForeignKey("parts.part_no", ondelete="CASCADE")
    )
    hazard_status: Mapped[str | None] = mapped_column(Text)


def make_engine(echo: bool = False) -> Engine:
    """Build a SQLAlchemy engine from environment variables.

    Args:
        echo: Whether to echo SQL statements.

    Returns:
        A configured SQLAlchemy :class:`Engine`.
    """
    user = os.environ.get("POSTGRES_USER", "lg")
    password = os.environ.get("POSTGRES_PASSWORD", "lg_password")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    db = os.environ.get("POSTGRES_DB", "lg_bom")
    url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
    return create_engine(url, echo=echo)
