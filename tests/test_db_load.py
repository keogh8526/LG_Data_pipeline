"""Tests for Step 5 — DB load + rollback against an in-memory SQLite engine.

Embedding / pgvector / pg_trgm logic is Postgres-only and exercised separately
via SQL-string assertions (test_db_search). Here we verify the relational
core: schema bootstrap, transactional load, idempotency, and rollback.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from src.db.engine import init_db
from src.db.load import LoadResult, load_run, read_committed_run, update_embeddings
from src.db.models import (
    BomEdge,
    ChangeEvent,
    EventDetail,
    Model,
    Part,
    PreprocessingRun,
)
from src.db.rollback import rollback_run


def _make_engine() -> object:
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    return engine


def _processed_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "base_part_no": "AB1234567",
        "new_part_no": "AB1234568",
        "part_name": "Bracket",
        "bom_level": 2,
        "part_type": "단품",
        "change_type": "Change",
        "change_point": "내열 보강",
        "change_reason": "필드 불량",
        "qty": 1.0,
        "model_code": "WSED7667M.ABMQEUR",
        "source_file": "raw/foo.xlsx",
        "form_version": "96col",
        "run_id": "run_test",
        "extracted_at": "2026-05-24T00:00:00+00:00",
        "_quarantine_reason": None,
    }
    base.update(overrides)
    return base


def _write_committed_run(tmp_path: Path, run_id: str, rows: list[dict[str, object]]) -> Path:
    run_dir = tmp_path / run_id
    files_dir = run_dir / "files"
    files_dir.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(files_dir / "foo.parquet", index=False)
    return run_dir


def test_init_db_creates_all_tables() -> None:
    engine = _make_engine()
    with engine.connect() as conn:
        names = {row[0] for row in conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert {
        "parts",
        "models",
        "bom_edges",
        "change_events",
        "event_details",
        "preprocessing_runs",
    } <= names


def test_read_committed_run_filters_quarantined(tmp_path: Path) -> None:
    run_dir = _write_committed_run(
        tmp_path,
        "run_x",
        [
            _processed_row(),
            _processed_row(
                base_part_no="123",
                _quarantine_reason="base_part_no: post_validate failed",
            ),
        ],
    )
    df = read_committed_run(run_dir)
    assert len(df) == 1
    assert df["base_part_no"].iloc[0] == "AB1234567"


def test_load_run_inserts_parts_models_events(tmp_path: Path) -> None:
    run_dir = _write_committed_run(
        tmp_path,
        "run_x",
        [
            _processed_row(),
            _processed_row(base_part_no="AB1234569", new_part_no="AB1234570"),
        ],
    )
    engine = _make_engine()
    with Session(engine) as session:
        result = load_run(session, "run_x", run_dir)
    assert isinstance(result, LoadResult)
    assert result.rows_inserted["change_events"] == 2

    with Session(engine) as session:
        assert session.execute(select(func.count()).select_from(Part)).scalar_one() == 4
        assert session.execute(select(func.count()).select_from(Model)).scalar_one() == 1
        assert session.execute(select(func.count()).select_from(ChangeEvent)).scalar_one() == 2
        run_row = session.get(PreprocessingRun, "run_x")
        assert run_row is not None and run_row.status == "committed"
        # Parsed model components populated from parse_model_code.
        model = session.get(Model, "WSED7667M.ABMQEUR")
        assert model is not None
        assert model.region == "EUR"


def test_load_run_rejects_double_load(tmp_path: Path) -> None:
    run_dir = _write_committed_run(tmp_path, "run_x", [_processed_row()])
    engine = _make_engine()
    with Session(engine) as session:
        load_run(session, "run_x", run_dir)
    with Session(engine) as session:
        with pytest.raises(ValueError, match="already loaded"):
            load_run(session, "run_x", run_dir)


def test_load_run_empty_dir_raises(tmp_path: Path) -> None:
    run_dir = tmp_path / "empty"
    (run_dir / "files").mkdir(parents=True)
    engine = _make_engine()
    with Session(engine) as session:
        with pytest.raises(ValueError, match="no clean rows"):
            load_run(session, "run_x", run_dir)


def test_rollback_run_deletes_change_events_only(tmp_path: Path) -> None:
    run_dir = _write_committed_run(tmp_path, "run_x", [_processed_row()])
    engine = _make_engine()
    with Session(engine) as session:
        load_run(session, "run_x", run_dir)
    with Session(engine) as session:
        result = rollback_run(session, "run_x")
    assert result.rows_deleted["change_events"] == 1
    with Session(engine) as session:
        # Parts and models survive (other runs may reference them).
        assert session.execute(select(func.count()).select_from(Part)).scalar_one() == 2
        assert session.execute(select(func.count()).select_from(ChangeEvent)).scalar_one() == 0
        run_row = session.get(PreprocessingRun, "run_x")
        assert run_row is not None and run_row.status == "rolled_back"


def test_rollback_unknown_run_raises() -> None:
    engine = _make_engine()
    with Session(engine) as session:
        with pytest.raises(ValueError, match="unknown run"):
            rollback_run(session, "missing")


def test_same_part_in_two_runs_upserts(tmp_path: Path) -> None:
    engine = _make_engine()
    run_a = _write_committed_run(
        tmp_path, "run_a", [_processed_row(part_name="Bracket-A")]
    )
    run_b = _write_committed_run(
        tmp_path, "run_b", [_processed_row(part_name="Bracket-B")]
    )
    with Session(engine) as session:
        load_run(session, "run_a", run_a)
    with Session(engine) as session:
        load_run(session, "run_b", run_b)
    with Session(engine) as session:
        # Still exactly two Part rows (base + new from same row, shared
        # across runs); the latest run's metadata wins.
        assert session.execute(select(func.count()).select_from(Part)).scalar_one() == 2
        part = session.get(Part, "AB1234567")
        assert part is not None
        assert part.run_id == "run_b"
        assert part.part_name == "Bracket-B"
        # Two distinct events though.
        assert session.execute(select(func.count()).select_from(ChangeEvent)).scalar_one() == 2


def test_update_embeddings_gated_by_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_EMBEDDING", raising=False)
    engine = _make_engine()
    with Session(engine) as session:
        with pytest.raises(RuntimeError, match="embedding disabled"):
            update_embeddings(session, "run_x")


def test_event_details_table_present(tmp_path: Path) -> None:
    engine = _make_engine()
    with Session(engine) as session:
        assert session.execute(select(func.count()).select_from(EventDetail)).scalar_one() == 0
        assert session.execute(select(func.count()).select_from(BomEdge)).scalar_one() == 0
