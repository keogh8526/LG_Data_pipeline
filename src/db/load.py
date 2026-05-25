"""Step 5 — load a committed run into PostgreSQL.

The committed-run directory holds one parquet per source file (Step 4
``commit_run``); this module reads them, derives parts / models / change
events, and inserts them transactionally. ``run_id`` is the batch identifier
on every table so rollback is a single ``WHERE run_id = ?`` per FK level.

Embedding generation is a separate concern wired in :func:`update_embeddings`:
gated by ``ENABLE_EMBEDDING=1`` so unit tests never trigger a model download
or a network call.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.db.models import (
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
    """Counts inserted / upserted per table."""

    run_id: str
    rows_inserted: dict[str, int] = field(default_factory=dict)


def read_committed_run(run_dir: Path) -> pd.DataFrame:
    """Concatenate every per-file parquet under ``run_dir/files/``.

    Args:
        run_dir: A committed-run directory (``committed/<run_id>``).

    Returns:
        The combined DataFrame. Rows whose ``_quarantine_reason`` is non-null
        are filtered out — those belong to the quarantine store, not the DB.
    """
    files_dir = run_dir / "files"
    if not files_dir.exists():
        raise FileNotFoundError(f"no files/ under {run_dir}")
    frames = [pd.read_parquet(p) for p in sorted(files_dir.glob("*.parquet"))]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "_quarantine_reason" in df.columns:
        df = df[df["_quarantine_reason"].isna()].reset_index(drop=True)
    return df


def _row_to_json_safe(row: pd.Series) -> dict[str, object]:
    """Convert a Series to a dict whose values are JSON-serializable."""
    return json.loads(row.to_json())


def _present(value: object) -> bool:
    """True for non-null, non-empty values."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _build_parts(df: pd.DataFrame, run_id: str) -> dict[str, Part]:
    """Distinct {base_part_no, new_part_no} -> Part rows."""
    parts: dict[str, Part] = {}
    # Enrich with name / level / type from the row where each part first appears.
    for _, row in df.iterrows():
        for col in ("base_part_no", "new_part_no"):
            value = row.get(col)
            if not _present(value):
                continue
            value = str(value)
            if value in parts:
                continue
            parts[value] = Part(
                part_no=value,
                part_name=row.get("part_name") if _present(row.get("part_name")) else None,
                part_type=row.get("part_type") if _present(row.get("part_type")) else None,
                bom_level=(
                    int(row["bom_level"])
                    if _present(row.get("bom_level"))
                    else None
                ),
                source_file=row.get("source_file"),
                form_version=row.get("form_version"),
                run_id=run_id,
            )
    return parts


def _build_models(df: pd.DataFrame, run_id: str) -> dict[str, Model]:
    """Distinct model_code -> Model rows with parsed components."""
    models: dict[str, Model] = {}
    for value in df["model_code"].dropna().unique():
        code = str(value)
        if not code.strip() or code in models:
            continue
        parsed = parse_model_code(code)
        models[code] = Model(
            model_code=code,
            model_name=parsed.model_name if parsed else None,
            grade_suffix=parsed.grade_suffix if parsed else None,
            region=parsed.region if parsed else None,
            run_id=run_id,
        )
    return models


def _build_change_event(row: pd.Series, run_id: str) -> ChangeEvent:
    """Map one processed row to a :class:`ChangeEvent` instance."""
    return ChangeEvent(
        base_part_no=row.get("base_part_no") if _present(row.get("base_part_no")) else None,
        new_part_no=row.get("new_part_no") if _present(row.get("new_part_no")) else None,
        model_code=row.get("model_code") if _present(row.get("model_code")) else None,
        change_type=row.get("change_type") if _present(row.get("change_type")) else None,
        bom_level=(
            int(row["bom_level"]) if _present(row.get("bom_level")) else None
        ),
        change_point=row.get("change_point") if _present(row.get("change_point")) else None,
        change_reason=row.get("change_reason") if _present(row.get("change_reason")) else None,
        form_version=row.get("form_version") if _present(row.get("form_version")) else None,
        source_file=row.get("source_file") if _present(row.get("source_file")) else None,
        raw_data=_row_to_json_safe(row),
        run_id=run_id,
    )


def load_run(
    session: Session,
    run_id: str,
    run_dir: Path,
) -> LoadResult:
    """Load one committed run into the relational tables transactionally.

    Args:
        session: An open SQLAlchemy session.
        run_id: Batch identifier — must not already exist in
            ``preprocessing_runs``.
        run_dir: The committed-run directory.

    Returns:
        A :class:`LoadResult` with per-table insert counts.

    Raises:
        ValueError: If the run is already loaded.
    """
    existing = session.get(PreprocessingRun, run_id)
    if existing is not None:
        raise ValueError(f"run {run_id} already loaded")

    df = read_committed_run(run_dir)
    if df.empty:
        raise ValueError(f"no clean rows to load under {run_dir}")

    parts = _build_parts(df, run_id)
    models = _build_models(df, run_id)
    events = [_build_change_event(row, run_id) for _, row in df.iterrows()]

    # Upsert parts / models (PK conflicts get the latest values).
    for part in parts.values():
        session.merge(part)
    for model in models.values():
        session.merge(model)
    session.flush()

    # Append-only change events.
    session.add_all(events)
    session.flush()

    rows_inserted = {
        "parts": len(parts),
        "models": len(models),
        "change_events": len(events),
        "event_details": 0,
        "bom_edges": 0,
    }
    session.add(
        PreprocessingRun(
            run_id=run_id,
            status="committed",
            files_processed={"files": sorted(p.name for p in (run_dir / "files").glob("*.parquet"))},
            rows_inserted=rows_inserted,
        )
    )
    session.commit()
    log.info("db.load.done", run_id=run_id, rows=rows_inserted)
    return LoadResult(run_id=run_id, rows_inserted=rows_inserted)


def update_embeddings(session: Session, run_id: str) -> int:
    """Generate and store embeddings for one run's change events.

    Args:
        session: An open SQLAlchemy session.
        run_id: Batch identifier; only events for this run are processed.

    Returns:
        Number of events embedded.

    Raises:
        RuntimeError: If ``ENABLE_EMBEDDING != 1`` (gated to keep tests fast).
    """
    if os.environ.get("ENABLE_EMBEDDING", "0") != "1":
        raise RuntimeError(
            "embedding disabled — set ENABLE_EMBEDDING=1 and run Ollama"
        )

    from src.embed.embedder import embed_texts

    events = (
        session.execute(select(ChangeEvent).where(ChangeEvent.run_id == run_id))
        .scalars()
        .all()
    )
    texts = [e.change_point or "" for e in events]
    embeddings = embed_texts(texts)
    update_sql = text(
        "UPDATE change_events SET change_point_embedding = :vec "
        "WHERE event_id = :event_id"
    )
    for event, vector in zip(events, embeddings, strict=True):
        session.execute(
            update_sql, {"vec": str(vector), "event_id": event.event_id}
        )
    session.commit()
    log.info("db.embeddings.updated", run_id=run_id, events=len(events))
    return len(events)
