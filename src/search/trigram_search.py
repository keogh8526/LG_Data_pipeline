"""v2.0 §7-3 — sparse trigram retrieval (pg_trgm).

``narrative_text``에 대해 ``%`` similarity 매칭. Postgres 전용 (pg_trgm).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class SparseHit:
    event_id: str
    score: float


_SQL = """
SELECT event_id, similarity(narrative_text, :q) AS score
FROM change_events
WHERE narrative_text %% :q
  AND (:form_ver IS NULL OR form_version = :form_ver)
ORDER BY similarity(narrative_text, :q) DESC
LIMIT :k
"""


def trigram_search(
    session: Session,
    query: str,
    k: int = 30,
    form_version: str | None = None,
) -> list[SparseHit]:
    if not query.strip():
        return []
    result = session.execute(
        text(_SQL),
        {"q": query, "k": k, "form_ver": form_version},
    )
    return [SparseHit(event_id=str(r[0]), score=float(r[1])) for r in result]
