"""Step 5 — DB rollback for a loaded run.

Mirrors :func:`src.preprocess.pipeline.rollback_run` at the DB layer: deletes
the ``change_events`` and ``event_details`` rows for the given ``run_id`` and
flips the corresponding :class:`PreprocessingRun` to ``rolled_back``.

Parts and models are *not* removed because another run may still reference
them (the latest run's metadata stays on the row). Reintroducing them later
is harmless (``MERGE`` semantics in :func:`load_run`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.db.models import BomEdge, ChangeEvent, EventDetail, PreprocessingRun
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class RollbackResult:
    """Counts deleted per table for a single rollback."""

    run_id: str
    rows_deleted: dict[str, int] = field(default_factory=dict)


def rollback_run(session: Session, run_id: str) -> RollbackResult:
    """Delete the change events / details / bom edges loaded under ``run_id``.

    Args:
        session: An open SQLAlchemy session.
        run_id: Batch identifier (must already exist in ``preprocessing_runs``).

    Returns:
        A :class:`RollbackResult` with per-table delete counts.

    Raises:
        ValueError: If no run row exists for ``run_id``.
    """
    run_row = session.get(PreprocessingRun, run_id)
    if run_row is None:
        raise ValueError(f"unknown run: {run_id}")

    deleted: dict[str, int] = {}
    deleted["event_details"] = (
        session.execute(
            delete(EventDetail).where(EventDetail.run_id == run_id)
        ).rowcount
        or 0
    )
    deleted["change_events"] = (
        session.execute(
            delete(ChangeEvent).where(ChangeEvent.run_id == run_id)
        ).rowcount
        or 0
    )
    deleted["bom_edges"] = (
        session.execute(
            delete(BomEdge).where(BomEdge.run_id == run_id)
        ).rowcount
        or 0
    )

    run_row.status = "rolled_back"
    run_row.committed_at = run_row.committed_at  # keep original
    run_row.rows_inserted = {
        **(run_row.rows_inserted or {}),
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
        "rows_deleted": deleted,
    }
    session.commit()
    log.info("db.rollback.done", run_id=run_id, deleted=deleted)
    return RollbackResult(run_id=run_id, rows_deleted=deleted)
