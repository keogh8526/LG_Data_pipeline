"""Step 5 — hybrid search (pgvector + pg_trgm).

Combines a semantic side (pgvector cosine over ``change_point_embedding``)
with a lexical side (``pg_trgm`` similarity over ``change_point``) inside one
Cypher-style CTE and ranks results by a weighted sum. ``form_version`` is an
optional metadata filter (VersionRAG pattern).

This is Postgres-only — it uses ``vector``, ``%`` and ``similarity()``.
SQLite tests assert that the SQL builds correctly (no execution).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.utils.logging import get_logger

log = get_logger(__name__)

# Weighted-sum fusion: 70% semantic + 30% lexical.
SEMANTIC_WEIGHT = 0.7
LEXICAL_WEIGHT = 0.3
CANDIDATE_LIMIT = 50

HYBRID_SQL = f"""
WITH semantic AS (
    SELECT event_id,
           1 - (change_point_embedding <=> CAST(:emb AS vector)) AS sem_score
    FROM change_events
    WHERE change_point_embedding IS NOT NULL
      AND (:form_ver IS NULL OR form_version = :form_ver)
    ORDER BY change_point_embedding <=> CAST(:emb AS vector)
    LIMIT {CANDIDATE_LIMIT}
),
lexical AS (
    SELECT event_id, similarity(change_point, :q) AS lex_score
    FROM change_events
    WHERE change_point %% :q
      AND (:form_ver IS NULL OR form_version = :form_ver)
    LIMIT {CANDIDATE_LIMIT}
)
SELECT ce.event_id, ce.change_point, ce.form_version,
       COALESCE(s.sem_score, 0) * {SEMANTIC_WEIGHT}
         + COALESCE(l.lex_score, 0) * {LEXICAL_WEIGHT} AS score
FROM change_events ce
LEFT JOIN semantic s USING (event_id)
LEFT JOIN lexical l USING (event_id)
WHERE s.event_id IS NOT NULL OR l.event_id IS NOT NULL
ORDER BY score DESC
LIMIT :k
"""


@dataclass
class SearchHit:
    """One row of a hybrid-search result."""

    event_id: int
    change_point: str | None
    form_version: str | None
    score: float


def hybrid_search(
    session: Session,
    query: str,
    *,
    top_k: int = 10,
    form_version: str | None = None,
) -> list[SearchHit]:
    """Run the hybrid query against Postgres.

    Args:
        session: An open SQLAlchemy session bound to a Postgres engine.
        query: Free-text query (used both for embedding and trigram side).
        top_k: Max rows to return.
        form_version: Optional VersionRAG filter.

    Returns:
        Ranked :class:`SearchHit` results (empty list if nothing matched).
    """
    from src.embed.embedder import embed_texts

    embedding = embed_texts([query])[0]
    result = session.execute(
        text(HYBRID_SQL),
        {
            "emb": str(embedding),
            "q": query,
            "form_ver": form_version,
            "k": top_k,
        },
    )
    hits: list[SearchHit] = []
    for row in result:
        hits.append(
            SearchHit(
                event_id=int(row.event_id),
                change_point=row.change_point,
                form_version=row.form_version,
                score=float(row.score),
            )
        )
    log.info("db.search.done", q_len=len(query), hits=len(hits))
    return hits
