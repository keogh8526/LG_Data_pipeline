"""Tests for the Step 5 PostgreSQL schema (no live database required)."""

from __future__ import annotations

from src.load.sql_schema import Base, ChangeEvent


def test_seven_core_tables_defined() -> None:
    expected = {
        "parts",
        "models",
        "bom_edges",
        "change_events",
        "part_grades",
        "test_plans",
        "hsms_records",
    }
    assert expected <= set(Base.metadata.tables)


def test_change_events_has_provenance_columns() -> None:
    cols = {c.name for c in ChangeEvent.__table__.columns}
    assert {"source_file", "form_version", "created_at", "updated_at"} <= cols


def test_change_events_has_jsonb_raw_data() -> None:
    assert "raw_data" in {c.name for c in ChangeEvent.__table__.columns}
