"""Step 7 — Neo4j hybrid search (vector + graph in a single Cypher).

For the MVP, Neo4j is the single store: native vector indexes provide
similarity search and are combined with graph traversal in one query — no
app-layer fusion. The VersionRAG pattern up-weights hits whose ``form_version``
matches the query's preferred version.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class SearchHit:
    """A single retrieval result."""

    event_id: str
    score: float
    change_point: str | None = None
    form_version: str | None = None
    related: dict[str, object] = field(default_factory=dict)


# Hybrid retrieval: native vector search expanded with a 1-hop graph pattern,
# all in one Cypher query. ``$index`` is a vector index name, ``$vec`` the
# query embedding, ``$k`` the cutoff.
HYBRID_QUERY = """
CALL db.index.vector.queryNodes($index, $k, $vec)
YIELD node AS e, score
OPTIONAL MATCH (e)-[:CHANGED_FROM]->(p_from:Part)
OPTIONAL MATCH (e)-[:CHANGED_TO]->(p_to:Part)
OPTIONAL MATCH (e)-[:BELONGS_TO]->(m:Model)
RETURN e.event_id AS event_id, score,
       e.change_point AS change_point, e.form_version AS form_version,
       p_from.part_no AS base_part_no, p_to.part_no AS new_part_no,
       m.model_code AS model_code
ORDER BY score DESC
"""


def apply_version_weight(
    hits: list[SearchHit],
    preferred_version: str | None,
    boost: float = 1.2,
) -> list[SearchHit]:
    """Up-weight hits whose form version matches the preferred version.

    Args:
        hits: Search hits to re-score.
        preferred_version: The version to boost, or None for no boost.
        boost: Multiplicative boost factor.

    Returns:
        Re-scored, re-sorted hits.
    """
    if preferred_version is None:
        return hits
    for hit in hits:
        if hit.form_version == preferred_version:
            hit.score *= boost
    return sorted(hits, key=lambda h: h.score, reverse=True)


def hybrid_search(
    driver: object,
    query_vector: list[float],
    index: str = "change_point_vec",
    top_k: int = 10,
    preferred_version: str | None = None,
) -> list[SearchHit]:
    """Run a hybrid vector + graph search against Neo4j.

    Args:
        driver: A Neo4j driver.
        query_vector: The dense query embedding.
        index: Vector index name to query.
        top_k: Number of results to return.
        preferred_version: Optional form version to up-weight (VersionRAG).

    Returns:
        Ranked :class:`SearchHit` results.
    """
    hits: list[SearchHit] = []
    with driver.session() as session:  # type: ignore[attr-defined]
        result = session.run(
            HYBRID_QUERY, index=index, k=top_k, vec=query_vector
        )
        for record in result:
            hits.append(
                SearchHit(
                    event_id=record["event_id"],
                    score=float(record["score"]),
                    change_point=record["change_point"],
                    form_version=record["form_version"],
                    related={
                        "base_part_no": record["base_part_no"],
                        "new_part_no": record["new_part_no"],
                        "model_code": record["model_code"],
                    },
                )
            )
    hits = apply_version_weight(hits, preferred_version)
    log.info("search.hybrid", index=index, results=len(hits))
    return hits
