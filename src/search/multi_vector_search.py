"""v2.0 §7-3 — multi-vector dense retrieval (pgvector).

각 벡터 컬럼(narrative_emb/change_point_emb/...)에 대해 별도 top-N을 가져온
뒤, 결과를 weighted-sum하여 통합. RRF는 별도 모듈(``pipeline.reciprocal_rank_fusion``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class DenseHit:
    """dense 검색 한 결과."""

    event_id: str
    score: float
    source_vector: str


_BASE_SQL = """
SELECT event_id, 1 - ({col} <=> CAST(:vec AS vector)) AS score
FROM change_events
WHERE {col} IS NOT NULL
  AND (:form_ver IS NULL OR form_version = :form_ver)
ORDER BY {col} <=> CAST(:vec AS vector)
LIMIT :k
"""


def single_vector_search(
    session: Session,
    vector_col: str,
    query_vec: Sequence[float],
    k: int = 30,
    form_version: str | None = None,
) -> list[DenseHit]:
    """단일 벡터 컬럼에 대한 top-k 코사인 검색."""
    sql = text(_BASE_SQL.format(col=vector_col))
    result = session.execute(
        sql,
        {"vec": str(list(query_vec)), "k": k, "form_ver": form_version},
    )
    return [
        DenseHit(event_id=str(r[0]), score=float(r[1]), source_vector=vector_col)
        for r in result
    ]


def multi_vector_search(
    session: Session,
    query_vec: Sequence[float],
    weights: dict[str, float],
    k: int = 30,
    form_version: str | None = None,
) -> list[DenseHit]:
    """가중치 기반 multi-vector 검색.

    각 벡터별 top-k 수집 → event_id별 가중 점수 합산 → 상위 k 반환.
    """
    if not weights:
        return []
    aggregated: dict[str, float] = {}
    primary: dict[str, str] = {}  # event_id → 첫 검출 vector
    for col, w in weights.items():
        hits = single_vector_search(session, col, query_vec, k=k, form_version=form_version)
        for h in hits:
            aggregated[h.event_id] = aggregated.get(h.event_id, 0.0) + h.score * w
            primary.setdefault(h.event_id, h.source_vector)

    fused = sorted(
        (
            DenseHit(event_id=eid, score=score, source_vector=primary.get(eid, "narrative_emb"))
            for eid, score in aggregated.items()
        ),
        key=lambda h: h.score,
        reverse=True,
    )
    return fused[:k]
