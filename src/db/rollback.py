"""v2.0 Step 5 — 적재된 run의 DB 롤백.

``src.preprocess.pipeline.rollback_run``의 DB 짝. 주어진 ``run_id``의
change_events / bom_edges / test_plans / hsms_records / needs_review_queue
행을 삭제하고 ``preprocessing_runs.status``를 ``rolled_back``로 전환.

parts / models는 다른 run이 참조할 수 있어 삭제하지 않음 (``MERGE`` 의미 유지).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.db.models import (
    BomEdge,
    ChangeEvent,
    NeedsReview,
    PreprocessingRun,
)
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class RollbackResult:
    """테이블별 삭제 결과."""

    run_id: str
    rows_deleted: dict[str, int] = field(default_factory=dict)


def rollback_run(session: Session, run_id: str) -> RollbackResult:
    """``run_id``의 적재 행 일괄 삭제.

    Raises:
        ValueError: 해당 run이 ``preprocessing_runs``에 없으면.
    """
    run_row = session.get(PreprocessingRun, run_id)
    if run_row is None:
        raise ValueError(f"unknown run: {run_id}")

    deleted: dict[str, int] = {}
    # FK 의존성 역순 삭제 (test_plans / hsms_records는 D-011로 제거됨).
    for label, model_cls in (
        ("needs_review_queue", NeedsReview),
        ("change_events", ChangeEvent),
        ("bom_edges", BomEdge),
    ):
        deleted[label] = (
            session.execute(delete(model_cls).where(model_cls.run_id == run_id)).rowcount or 0
        )

    run_row.status = "rolled_back"
    run_row.rows_inserted = {
        **(run_row.rows_inserted or {}),
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
        "rows_deleted": deleted,
    }
    session.commit()
    log.info("db.rollback.done", run_id=run_id, deleted=deleted)
    return RollbackResult(run_id=run_id, rows_deleted=deleted)
