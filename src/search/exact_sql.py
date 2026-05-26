"""v2.0 §7 — 식별자 패턴 매칭 시 SQL 직진 (벡터 우회).

part_no/model_code 정규식 매치 → b-tree 인덱스로 정확 조회.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import ChangeEvent


@dataclass
class ExactHit:
    event_id: str
    score: float
    part_no: str | None = None
    new_model_code: str | None = None


def exact_search(
    session: Session,
    field: str,
    values: Sequence[str],
    limit: int = 10,
) -> list[ExactHit]:
    """``field`` (part_no/base_part_no/new_model_code) 정확 일치 조회."""
    if not values:
        return []
    col = {
        "part_no": ChangeEvent.part_no,
        "base_part_no": ChangeEvent.base_part_no,
        "new_model_code": ChangeEvent.new_model_code,
    }.get(field)
    if col is None:
        return []

    stmt = (
        select(ChangeEvent)
        .where(col.in_(list(values)))
        .order_by(ChangeEvent.created_at.desc())
        .limit(limit)
    )
    rows = session.execute(stmt).scalars().all()
    return [
        ExactHit(
            event_id=str(r.event_id),
            score=1.0,
            part_no=r.part_no,
            new_model_code=r.new_model_code,
        )
        for r in rows
    ]
