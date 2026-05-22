"""Step 7 — Qdrant collection management and hybrid search.

Dense (embedding) and sparse (BM25) results are fused with Reciprocal Rank
Fusion. The VersionRAG pattern up-weights hits whose ``form_version`` matches
the query's preferred version.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from src.utils.logging import get_logger

log = get_logger(__name__)

COLLECTION_NAME = "change_events_text"

# Metadata payload fields stored alongside each vector.
PAYLOAD_FIELDS: tuple[str, ...] = (
    "event_id",
    "part_no",
    "model_code",
    "form_version",
    "grade",
    "region",
    "change_type",
    "source_file",
    "created_at",
)


@dataclass
class SearchHit:
    """A single retrieval result."""

    event_id: str
    score: float
    payload: dict[str, object]


def reciprocal_rank_fusion(
    dense_ranking: list[str],
    sparse_ranking: list[str],
    k: int = 60,
) -> list[tuple[str, float]]:
    """Fuse two ranked id lists with Reciprocal Rank Fusion.

    Args:
        dense_ranking: Ids ordered best-first from dense search.
        sparse_ranking: Ids ordered best-first from sparse (BM25) search.
        k: RRF dampening constant.

    Returns:
        ``(id, fused_score)`` pairs ordered best-first.
    """
    scores: dict[str, float] = {}
    for ranking in (dense_ranking, sparse_ranking):
        for rank, doc_id in enumerate(ranking):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def apply_version_weight(
    hits: list[tuple[str, float]],
    hit_versions: dict[str, str],
    preferred_version: str | None,
    boost: float = 1.2,
) -> list[tuple[str, float]]:
    """Up-weight hits whose form version matches the preferred version.

    Args:
        hits: ``(id, score)`` pairs.
        hit_versions: Map from id to its ``form_version``.
        preferred_version: The version to boost, or None for no boost.
        boost: Multiplicative boost factor.

    Returns:
        Re-scored and re-sorted ``(id, score)`` pairs.
    """
    if preferred_version is None:
        return hits
    rescored = [
        (doc_id, score * boost if hit_versions.get(doc_id) == preferred_version else score)
        for doc_id, score in hits
    ]
    return sorted(rescored, key=lambda kv: kv[1], reverse=True)


def make_client() -> object:
    """Create a Qdrant client from environment variables.

    Returns:
        A ``QdrantClient`` instance.
    """
    from qdrant_client import QdrantClient

    host = os.environ.get("QDRANT_HOST", "localhost")
    port = int(os.environ.get("QDRANT_PORT", "6333"))
    return QdrantClient(host=host, port=port)


def ensure_collection(client: object, dim: int) -> None:
    """Create the change-events collection if it does not exist.

    Args:
        client: A Qdrant client.
        dim: Dense vector dimensionality.
    """
    from qdrant_client.models import Distance, VectorParams

    existing = {c.name for c in client.get_collections().collections}  # type: ignore[attr-defined]
    if COLLECTION_NAME in existing:
        return
    client.create_collection(  # type: ignore[attr-defined]
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )
    log.info("embed.collection_created", name=COLLECTION_NAME, dim=dim)
