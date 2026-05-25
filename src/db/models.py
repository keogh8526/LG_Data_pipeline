"""Step 5 — SQLAlchemy ORM models (the relational core).

These models cover everything except the pgvector embedding columns and
pg_trgm indexes — those live in ``src/db/schema.sql`` and are applied to
Postgres separately. Keeping vectors out of the ORM means the same models
work against SQLite for fast unit tests.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every table."""


class Part(Base):
    """One distinct part-number across the corpus."""

    __tablename__ = "parts"

    part_no: Mapped[str] = mapped_column(String(15), primary_key=True)
    part_name: Mapped[str | None] = mapped_column(Text, default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    part_type: Mapped[str | None] = mapped_column(String(50), default=None)
    bom_level: Mapped[int | None] = mapped_column(Integer, default=None)
    source_file: Mapped[str | None] = mapped_column(Text, default=None)
    form_version: Mapped[str | None] = mapped_column(String(10), default=None)
    run_id: Mapped[str | None] = mapped_column(String(50), index=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class Model(Base):
    """One distinct model code with parsed name / region components."""

    __tablename__ = "models"

    model_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    model_name: Mapped[str | None] = mapped_column(String(30), default=None)
    grade_suffix: Mapped[str | None] = mapped_column(String(20), default=None)
    region: Mapped[str | None] = mapped_column(String(10), default=None)
    run_id: Mapped[str | None] = mapped_column(String(50), index=True, default=None)


class BomEdge(Base):
    """One parent->child relationship in a BOM tree. Populated by the BOM-tree
    extractor (not yet wired — see TODO(real-data))."""

    __tablename__ = "bom_edges"

    model_code: Mapped[str] = mapped_column(
        String(50), ForeignKey("models.model_code"), primary_key=True
    )
    parent_part_no: Mapped[str] = mapped_column(
        String(15), ForeignKey("parts.part_no"), primary_key=True
    )
    child_part_no: Mapped[str] = mapped_column(
        String(15), ForeignKey("parts.part_no"), primary_key=True
    )
    qty: Mapped[float | None] = mapped_column(Float, default=None)
    run_id: Mapped[str | None] = mapped_column(String(50), index=True, default=None)


class ChangeEvent(Base):
    """A single change-point row, append-only per run."""

    __tablename__ = "change_events"

    event_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    base_part_no: Mapped[str | None] = mapped_column(
        String(15), ForeignKey("parts.part_no"), default=None
    )
    new_part_no: Mapped[str | None] = mapped_column(
        String(15), ForeignKey("parts.part_no"), default=None
    )
    model_code: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("models.model_code"), default=None
    )
    change_type: Mapped[str | None] = mapped_column(String(20), default=None)
    bom_level: Mapped[int | None] = mapped_column(Integer, default=None)
    change_point: Mapped[str | None] = mapped_column(Text, default=None)
    change_reason: Mapped[str | None] = mapped_column(Text, default=None)
    form_version: Mapped[str | None] = mapped_column(String(10), default=None)
    source_file: Mapped[str | None] = mapped_column(Text, default=None)
    raw_data: Mapped[dict | None] = mapped_column(JSON, default=None)
    run_id: Mapped[str | None] = mapped_column(String(50), index=True, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )


class EventDetail(Base):
    """Auxiliary JSON details (DRBFM, FMEA, HSMS, ...) hung off a change event."""

    __tablename__ = "event_details"

    event_id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        ForeignKey("change_events.event_id", ondelete="CASCADE"),
        primary_key=True,
    )
    detail_type: Mapped[str] = mapped_column(String(30), primary_key=True)
    detail_json: Mapped[dict | None] = mapped_column(JSON, default=None)
    run_id: Mapped[str | None] = mapped_column(String(50), index=True, default=None)


class PreprocessingRun(Base):
    """One commit / load batch. The single source of truth for ``run_id``."""

    __tablename__ = "preprocessing_runs"

    run_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    committed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    status: Mapped[str | None] = mapped_column(String(20), default=None)
    files_processed: Mapped[dict | None] = mapped_column(JSON, default=None)
    validation_report: Mapped[dict | None] = mapped_column(JSON, default=None)
    golden_diff_report: Mapped[dict | None] = mapped_column(JSON, default=None)
    rows_inserted: Mapped[dict | None] = mapped_column(JSON, default=None)


__all__ = [
    "Base",
    "BomEdge",
    "ChangeEvent",
    "EventDetail",
    "Model",
    "Part",
    "PreprocessingRun",
]
