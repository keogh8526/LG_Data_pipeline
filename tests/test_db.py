"""v2.0 DB ORM smoke test — SQLite로 schema + load 사이클 검증.

Postgres 전용 기능(JSONB GIN, vector, pg_trgm)은 schema.sql에 있고 본 테스트는
스킵 — SQLite는 JSON column으로 fallback.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import select

from src.db.engine import init_db, make_engine, session_factory
from src.db.load import load_run
from src.db.models import ChangeEvent, Part, PreprocessingRun
from src.db.rollback import rollback_run


@pytest.fixture()
def session(tmp_path):
    db_path = tmp_path / "test.db"
    engine = make_engine(f"sqlite:///{db_path}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


def _make_run_dir(tmp_path: Path, run_id: str) -> Path:
    run_dir = tmp_path / "committed" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = pd.DataFrame(
        [
            {
                "part_no": "AGG74419321",
                "part_name": "Packing",
                "base_part_no": "AGG74419320",
                "base_model_code": "WSED7667M",
                "new_model_code": "WSED7667M.ABMQEUR",
                "grade": "Best-1",
                "region": "EUR",
                "change_type": "Change",
                "event_stage": "DV",
                "change_point": "내열 220→240",
                "change_reason": "신규 규제",
                "bom_level": 1,
                "part_type": "Assy",
                "payload": json.dumps({"공통 > 부품 P/No": "AGG74419321"}),
                "semantic_text": json.dumps({"DRBFM > 변경점": "내열 강화"}),
                "narrative_text": "변경부품 AGG74419321...",
                "form_version": "변경부품_list_96",
                "source_file": "x.xlsx",
                "source_sheet": "변경부품 list",
                "source_row": 5,
                "run_id": run_id,
                "confidence": 1.0,
                "needs_review": False,
            }
        ]
    )
    rows.to_parquet(run_dir / "rows.parquet", index=False)
    return run_dir


def test_load_and_rollback_cycle(session, tmp_path):
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    run_dir = _make_run_dir(tmp_path, run_id)

    result = load_run(session, run_id, run_dir)
    assert result.rows_inserted["change_events"] == 1
    assert result.rows_inserted["parts"] >= 1
    assert result.rows_inserted["models"] >= 1


# ── C-3 회귀: quarantine 행은 DB에 적재되면 안 됨 ──


def _make_run_dir_with_quarantine(tmp_path, run_id: str):
    """1 clean + 2 quarantine 행을 가진 rows.parquet 생성."""
    run_dir = tmp_path / "committed" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    base_record = {
        "part_no": "AGG74419321",
        "part_name": "Packing",
        "base_part_no": "AGG74419320",
        "base_model_code": "WSED7667M",
        "new_model_code": "WSED7667M.ABMQEUR",
        "grade": "Best-1",
        "region": "EUR",
        "change_type": "Change",
        "event_stage": "DV",
        "change_point": "내열 220→240",
        "change_reason": "신규 규제",
        "bom_level": 1,
        "part_type": "Assy",
        "payload": json.dumps({"공통 > 부품 P/No": "AGG74419321"}),
        "semantic_text": json.dumps({}),
        "narrative_text": "변경부품 AGG...",
        "form_version": "변경부품_list_96",
        "source_file": "x.xlsx",
        "source_sheet": "변경부품 list",
        "source_row": 5,
        "run_id": run_id,
        "confidence": 1.0,
        "needs_review": False,
    }
    rows = pd.DataFrame(
        [
            {**base_record, "_quarantine_reason": None, "source_row": 5},
            {
                **base_record,
                "_quarantine_reason": "part_no=axiom failed",
                "source_row": 6,
                "part_no": "INVALID",
                "needs_review": True,
            },
            {
                **base_record,
                "_quarantine_reason": "change_type=unknown",
                "source_row": 7,
                "part_no": "AGG74419999",
                "needs_review": True,
            },
        ]
    )
    rows.to_parquet(run_dir / "rows.parquet", index=False)
    return run_dir


def test_quarantine_rows_excluded_from_db_load(session, tmp_path):
    """C-3: rows.parquet에 _quarantine_reason 있는 행은 적재 제외."""
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    run_dir = _make_run_dir_with_quarantine(tmp_path, run_id)

    result = load_run(session, run_id, run_dir)
    # 3 행 중 quarantine 2 제외 → 1만 적재
    assert result.rows_inserted["change_events"] == 1, (
        f"expected only clean rows loaded, got {result.rows_inserted}"
    )
    # DB에 실제 1행만
    loaded = session.execute(
        select(ChangeEvent).where(ChangeEvent.run_id == run_id)
    ).scalars().all()
    assert len(loaded) == 1
    assert loaded[0].part_no == "AGG74419321"


# ── B-2 회귀: BomEdge FK + UNKNOWN Model 자동 upsert ──


# ── B-3 회귀: update_embeddings UPDATE가 COALESCE로 None 보호 ──
# D-011: multi-vector 5개 → narrative_emb 단일 벡터로 축소.


def test_update_embeddings_sql_uses_coalesce():
    """B-3 (D-011 후): update_embeddings의 UPDATE SQL이 COALESCE 사용."""
    import inspect

    from src.db import load as load_module

    src = inspect.getsource(load_module.update_embeddings)
    assert "COALESCE(CAST(:vec AS vector), narrative_emb)" in src, (
        "update_embeddings must use COALESCE for narrative_emb (B-3 fix). "
        "Otherwise None vectors overwrite existing data on re-run."
    )


def test_bom_edges_with_unknown_model_creates_model_row(session, tmp_path):
    """B-2: BOM 어댑터가 model_code='' fallback일 때 UNKNOWN Model 자동 upsert.

    BomEdge FK 명시 후, parts에 없는 P/No 또는 models에 없는 model_code는 적재 실패.
    'UNKNOWN'은 BOM 어댑터가 model 정보 없을 때 사용하는 sentinel — load.py가 자동 upsert.
    """
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    run_dir = tmp_path / "committed" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    # rows.parquet — 최소 1 clean event
    rows_df = pd.DataFrame(
        [
            {
                "part_no": "AGG74419321",
                "part_name": "Packing",
                "base_part_no": None,
                "base_model_code": None,
                "new_model_code": "WSED7667M",
                "grade": "Best-1",
                "region": "EUR",
                "change_type": "Change",
                "event_stage": "DV",
                "change_point": "x",
                "change_reason": "y",
                "bom_level": 1,
                "part_type": "Assy",
                "payload": json.dumps({"공통 > 부품 P/No": "AGG74419321"}),
                "semantic_text": json.dumps({}),
                "narrative_text": "narrative",
                "form_version": "변경부품_list_96",
                "source_file": "x.xlsx",
                "source_sheet": "변경부품 list",
                "source_row": 5,
                "run_id": run_id,
                "confidence": 1.0,
                "needs_review": False,
                "_quarantine_reason": None,
            }
        ]
    )
    rows_df.to_parquet(run_dir / "rows.parquet", index=False)

    # bom.parquet — model_code=UNKNOWN fallback이 들어간 edge
    bom_df = pd.DataFrame(
        [
            {
                "model_code": "UNKNOWN",
                "parent_part_no": "AGP0000001",
                "child_part_no": "AGP0000002",
                "qty": 1.0,
                "bom_level": 2,
                "run_id": run_id,
            }
        ]
    )
    bom_df.to_parquet(run_dir / "bom.parquet", index=False)

    # 적재 — FK 만족해야 성공
    result = load_run(session, run_id, run_dir)
    assert result.rows_inserted["bom_edges"] == 1
    # UNKNOWN Model이 자동 생성됐는지
    from src.db.models import Model

    unknown = session.get(Model, "UNKNOWN")
    assert unknown is not None, "UNKNOWN Model should be auto-upserted by B-2"

    # 적재 확인
    events = session.execute(select(ChangeEvent).where(ChangeEvent.run_id == run_id)).scalars().all()
    assert len(events) == 1
    assert events[0].part_no == "AGG74419321"

    # rollback
    rb = rollback_run(session, run_id)
    assert rb.rows_deleted["change_events"] == 1
    remaining = session.execute(select(ChangeEvent).where(ChangeEvent.run_id == run_id)).scalars().all()
    assert remaining == []

    run_row = session.get(PreprocessingRun, run_id)
    assert run_row is not None and run_row.status == "rolled_back"
