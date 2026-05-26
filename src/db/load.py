"""v2.0 Step 8 — committed run → PostgreSQL 트랜잭션 적재.

committed-run 디렉토리에는 ``rows.parquet`` (정규화된 ChangeEvent 행들)과
``bom.parquet`` (BOM 어댑터가 생성한 parts/edges)이 있다. 본 모듈은 이를
읽어 PG에 트랜잭션 적재한다. ``run_id``는 모든 테이블의 batch handle.

upsert vs insert-only:
  upsert: parts, models  (재실행 시 동일 부품·모델 갱신)
  insert-only: change_events, bom_edges  (이벤트는 시점 데이터)

임베딩은 별도 ``update_embeddings()``로 처리, ENABLE_EMBEDDING=1 게이트.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.db.models import (
    BomEdge,
    ChangeEvent,
    Model,
    Part,
    PreprocessingRun,
)
from src.preprocess.resolve import parse_model_code
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class LoadResult:
    """테이블별 적재 결과."""

    run_id: str
    rows_inserted: dict[str, int] = field(default_factory=dict)


# --- File IO -------------------------------------------------------------


ROWS_FILE = "rows.parquet"
BOM_FILE = "bom.parquet"
PARTS_FILE = "parts.parquet"
RESOLVED_FILE = "resolved.json"


def _present(value: object) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _coerce_json(value: object) -> Any:
    """parquet에서 읽은 JSON-serializable한 dict/list로 복원."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return value
    return value


def read_committed_run(run_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """committed run의 rows / bom parquet 로드.

    ``_quarantine_reason``이 채워진 행은 적재 대상이 아니므로 제외 (C-3 fix).
    quarantine된 행은 ``data/quarantine/<run_id>/``에 별도 저장돼 사람 검토용.

    BOM 어댑터는 parts/edges를 분리 저장 — parts.parquet과 bom.parquet 둘 다 인식.

    Returns:
        (events_df, bom_df). 없으면 빈 DataFrame.
    """
    rows = pd.DataFrame()
    bom_parts = pd.DataFrame()
    bom_edges = pd.DataFrame()
    p_rows = run_dir / ROWS_FILE
    p_bom = run_dir / BOM_FILE
    p_parts = run_dir / PARTS_FILE
    if p_rows.exists():
        rows = pd.read_parquet(p_rows)
        if "_quarantine_reason" in rows.columns:
            before = len(rows)
            rows = rows[rows["_quarantine_reason"].isna()].reset_index(drop=True)
            filtered = before - len(rows)
            if filtered:
                log.info("db.load.quarantine_filtered", rows=filtered)
    if p_bom.exists():
        bom_edges = pd.read_parquet(p_bom)
    if p_parts.exists():
        bom_parts = pd.read_parquet(p_parts)
    # bom_df는 edges 우선이지만 parts만 있으면 parts 반환 — _build_parts_from_bom이 활용.
    if not bom_edges.empty:
        return rows, bom_edges
    return rows, bom_parts


# --- Builders ------------------------------------------------------------


def _build_parts_from_events(df: pd.DataFrame, run_id: str) -> dict[str, Part]:
    """change_events의 part_no/base_part_no 집합 → Part 행."""
    parts: dict[str, Part] = {}
    for _, row in df.iterrows():
        for col in ("part_no", "base_part_no"):
            value = row.get(col)
            if not _present(value):
                continue
            key = str(value)
            if key in parts:
                continue
            parts[key] = Part(
                part_no=key,
                part_name=row.get("part_name") if _present(row.get("part_name")) else key,
                part_type=row.get("part_type") if _present(row.get("part_type")) else None,
                bom_level=int(row["bom_level"]) if _present(row.get("bom_level")) else None,
                source_file=row.get("source_file"),
                run_id=run_id,
                first_seen_run_id=run_id,
            )
    return parts


def _build_parts_from_bom(bom: pd.DataFrame, run_id: str, existing: dict[str, Part]) -> None:
    """BOM parquet에서 Part 추출.

    bom.parquet은 edges(parent/child) 또는 parts(part_no) 둘 중 하나 형식.
    둘 다 케이스를 처리해 existing dict 보강.
    """
    if bom.empty:
        return
    # 케이스 A: parts.parquet — part_no 컬럼이 직접 있음
    if "part_no" in bom.columns:
        for _, row in bom.iterrows():
            value = row.get("part_no")
            if not _present(value):
                continue
            key = str(value)
            if key in existing:
                continue
            existing[key] = Part(
                part_no=key,
                part_name=row.get("part_name") if _present(row.get("part_name")) else key,
                bom_level=int(row["bom_level"]) if _present(row.get("bom_level")) else None,
                part_type=row.get("part_type") if _present(row.get("part_type")) else None,
                source_file=row.get("source_file") if _present(row.get("source_file")) else None,
                run_id=run_id,
                first_seen_run_id=run_id,
            )
        return
    # 케이스 B: edges 형식 — parent/child 컬럼
    for _, row in bom.iterrows():
        for col in ("parent_part_no", "child_part_no"):
            value = row.get(col)
            if not _present(value):
                continue
            key = str(value)
            if key in existing:
                continue
            existing[key] = Part(
                part_no=key,
                part_name=row.get("part_name") if _present(row.get("part_name")) else key,
                bom_level=int(row["bom_level"]) if _present(row.get("bom_level")) else None,
                run_id=run_id,
                first_seen_run_id=run_id,
            )


def _build_models(df: pd.DataFrame, run_id: str) -> dict[str, Model]:
    models: dict[str, Model] = {}
    for col in ("new_model_code", "base_model_code"):
        if col not in df.columns:
            continue
        for value in df[col].dropna().unique():
            code = str(value).strip()
            if not code or code in models:
                continue
            parsed = parse_model_code(code)
            models[code] = Model(
                model_code=code,
                model_name=parsed.model_name or None,
                region=parsed.region,
                run_id=run_id,
            )
    return models


def _ensure_models_for_bom(
    bom: pd.DataFrame, run_id: str, existing: dict[str, Model]
) -> None:
    """B-2: bom_edges의 model_code FK 만족 위해 model row 보장.

    어댑터가 'UNKNOWN' fallback을 쓰는 경우도 포함 — UNKNOWN Model 자동 upsert.
    """
    if bom.empty or "model_code" not in bom.columns:
        return
    for value in bom["model_code"].dropna().unique():
        code = str(value).strip() or "UNKNOWN"
        if code in existing:
            continue
        if code == "UNKNOWN":
            existing[code] = Model(
                model_code=code,
                model_name="UNKNOWN",
                run_id=run_id,
            )
        else:
            parsed = parse_model_code(code)
            existing[code] = Model(
                model_code=code,
                model_name=parsed.model_name or None,
                region=parsed.region,
                run_id=run_id,
            )


def _build_change_event(row: pd.Series, run_id: str) -> ChangeEvent:
    """parquet row → ChangeEvent ORM 객체."""
    payload = _coerce_json(row.get("payload")) or {}
    semantic = _coerce_json(row.get("semantic_text")) or {}
    return ChangeEvent(
        part_no=row.get("part_no") if _present(row.get("part_no")) else None,
        part_name=row.get("part_name") if _present(row.get("part_name")) else None,
        base_part_no=row.get("base_part_no") if _present(row.get("base_part_no")) else None,
        base_model_code=row.get("base_model_code") if _present(row.get("base_model_code")) else None,
        new_model_code=row.get("new_model_code") if _present(row.get("new_model_code")) else None,
        grade=row.get("grade") if _present(row.get("grade")) else None,
        region=row.get("region") if _present(row.get("region")) else None,
        change_type=row.get("change_type") if _present(row.get("change_type")) else None,
        event_stage=row.get("event_stage") if _present(row.get("event_stage")) else None,
        change_point=row.get("change_point") if _present(row.get("change_point")) else None,
        change_reason=row.get("change_reason") if _present(row.get("change_reason")) else None,
        bom_level=int(row["bom_level"]) if _present(row.get("bom_level")) else None,
        part_type=row.get("part_type") if _present(row.get("part_type")) else None,
        payload=payload,
        semantic_text=semantic or None,
        narrative_text=row.get("narrative_text") if _present(row.get("narrative_text")) else None,
        form_version=row.get("form_version", "unknown"),
        source_file=row.get("source_file", ""),
        source_sheet=row.get("source_sheet", ""),
        source_row=int(row.get("source_row", 0)),
        run_id=run_id,
        confidence=float(row.get("confidence", 1.0)) if _present(row.get("confidence")) else 1.0,
        needs_review=bool(row.get("needs_review", False)),
    )


def _apply_resolution(
    run_dir: Path,
    parts: dict[str, Part],
    models: dict[str, Model],
) -> None:
    """I-3: ``resolved.json``의 ER 결과를 parts/models에 반영.

    - parts: aliases 컬럼 채움
    - models: 현재는 alias 컬럼이 ORM에 없어 grade/region만 보강 (auto_merge ≥ 0.95).
    파일 없으면 no-op.
    """
    path = run_dir / RESOLVED_FILE
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("db.load.resolved_unreadable", error=str(exc))
        return

    for record in data.get("parts", []):
        canonical = record.get("canonical_id")
        if canonical and canonical in parts:
            aliases = [a for a in record.get("aliases") or [] if a != canonical]
            if aliases:
                parts[canonical].aliases = aliases

    log.info(
        "db.load.resolved_applied",
        parts_with_aliases=sum(1 for p in parts.values() if p.aliases),
        models=len(models),
    )


def _build_bom_edges(bom: pd.DataFrame, run_id: str) -> list[BomEdge]:
    edges: list[BomEdge] = []
    if bom.empty:
        return edges
    seen: set[tuple[str, str, str]] = set()
    for _, row in bom.iterrows():
        model = str(row.get("model_code", "")).strip() or "UNKNOWN"
        parent = row.get("parent_part_no")
        child = row.get("child_part_no")
        if not (_present(parent) and _present(child)):
            continue
        key = (model, str(parent), str(child))
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            BomEdge(
                model_code=model,
                parent_part_no=str(parent),
                child_part_no=str(child),
                qty=float(row["qty"]) if _present(row.get("qty")) else None,
                bom_level=int(row["bom_level"]) if _present(row.get("bom_level")) else None,
                run_id=run_id,
            )
        )
    return edges


# --- Public --------------------------------------------------------------


def load_run(
    session: Session,
    run_id: str,
    run_dir: Path,
) -> LoadResult:
    """committed run을 트랜잭션 적재.

    Raises:
        ValueError: 이미 적재된 run이면.
    """
    existing = session.get(PreprocessingRun, run_id)
    if existing is not None:
        raise ValueError(f"run {run_id} already loaded")

    events_df, bom_df = read_committed_run(run_dir)
    if events_df.empty and bom_df.empty:
        # 일부 환경에서 parts.parquet만 있고 bom.parquet/rows.parquet 빈 경우 — 다시 확인
        # (이미 read_committed_run이 parts→bom_df fallback 처리하지만, 둘 다 없으면 정말 빈 run)
        # 빈 PreprocessingRun 기록만 남기고 종료
        log.warning("db.load.empty_run", run_dir=str(run_dir))
        session.add(
            PreprocessingRun(
                run_id=run_id,
                status="committed",
                committed_at=datetime.now(tz=timezone.utc),
                files_processed={},
                rows_inserted={"parts": 0, "models": 0, "change_events": 0, "bom_edges": 0},
            )
        )
        session.commit()
        return LoadResult(run_id=run_id, rows_inserted={"parts": 0, "models": 0, "change_events": 0, "bom_edges": 0})

    # ── Parts: change_events + bom edges 합집합 ──
    parts = _build_parts_from_events(events_df, run_id) if not events_df.empty else {}
    _build_parts_from_bom(bom_df, run_id, parts)

    models = _build_models(events_df, run_id) if not events_df.empty else {}
    _ensure_models_for_bom(bom_df, run_id, models)  # B-2: bom_edges FK 만족
    events = [_build_change_event(row, run_id) for _, row in events_df.iterrows()]
    edges = _build_bom_edges(bom_df, run_id)

    # I-3: ER 결과 적재 — resolved.json이 있으면 parts.aliases 갱신.
    _apply_resolution(run_dir, parts, models)

    # ── upsert parts/models ──
    for part in parts.values():
        session.merge(part)
    for model in models.values():
        session.merge(model)
    session.flush()

    # ── append-only events / edges ──
    if events:
        session.add_all(events)
    if edges:
        session.add_all(edges)
    session.flush()

    rows_inserted = {
        "parts": len(parts),
        "models": len(models),
        "change_events": len(events),
        "bom_edges": len(edges),
    }
    session.add(
        PreprocessingRun(
            run_id=run_id,
            status="committed",
            committed_at=datetime.now(tz=timezone.utc),
            files_processed={
                "events_file": ROWS_FILE if events else None,
                "bom_file": BOM_FILE if edges else None,
            },
            rows_inserted=rows_inserted,
        )
    )
    session.commit()
    log.info("db.load.done", run_id=run_id, rows=rows_inserted)
    return LoadResult(run_id=run_id, rows_inserted=rows_inserted)


# --- Embeddings ---------------------------------------------------------


# D-011: multi-vector (5 벡터) → narrative_emb 단일 벡터.
# BOM Agent의 retrieve는 단일 narrative embedding만 사용.


def update_embeddings(session: Session, run_id: str) -> int:
    """run 내 change_events에 narrative_emb 단일 임베딩 생성·저장.

    Raises:
        RuntimeError: ENABLE_EMBEDDING != 1.
    """
    if os.environ.get("ENABLE_EMBEDDING", "0") != "1":
        raise RuntimeError("embedding disabled - set ENABLE_EMBEDDING=1 and run Ollama")
    if session.bind is None or session.bind.dialect.name != "postgresql":
        log.warning("db.embed.skip_non_pg", dialect=getattr(session.bind, "dialect", None))
        return 0

    from src.embed.embedder import embed_texts

    events = (
        session.execute(select(ChangeEvent).where(ChangeEvent.run_id == run_id))
        .scalars()
        .all()
    )
    if not events:
        return 0

    texts = [(e.narrative_text or "") for e in events]
    vectors = embed_texts(texts)

    # B-3 유지: COALESCE로 NULL 덮어쓰기 방지 (재실행 시).
    update_sql = text(
        "UPDATE change_events SET "
        "narrative_emb = COALESCE(CAST(:vec AS vector), narrative_emb) "
        "WHERE event_id = :event_id"
    )
    for event, vec in zip(events, vectors, strict=True):
        session.execute(
            update_sql,
            {"event_id": event.event_id, "vec": str(vec) if vec else None},
        )
    session.commit()
    log.info("db.embeddings.updated", run_id=run_id, events=len(events))
    return len(events)
