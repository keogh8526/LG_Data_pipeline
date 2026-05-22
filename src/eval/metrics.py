"""Step 8 — evaluation metrics.

Deterministic metric functions for retrieval and schema-mapping evaluation.
All functions are pure; randomness elsewhere is seeded for reproducibility.
"""

from __future__ import annotations

import math

EVAL_SEED = 42


def recall_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    """Fraction of relevant items found within the top-k retrieved.

    Args:
        retrieved: Retrieved ids, best-first.
        relevant: Ground-truth relevant ids.
        k: Cutoff rank.

    Returns:
        Recall@k in ``[0, 1]``; 1.0 when there are no relevant items.
    """
    if not relevant:
        return 1.0
    top_k = set(retrieved[:k])
    return len(top_k & set(relevant)) / len(set(relevant))


def mean_reciprocal_rank(retrieved: list[str], relevant: list[str]) -> float:
    """Reciprocal rank of the first relevant item.

    Args:
        retrieved: Retrieved ids, best-first.
        relevant: Ground-truth relevant ids.

    Returns:
        ``1 / rank`` of the first hit, or 0.0 if none.
    """
    relevant_set = set(relevant)
    for rank, doc_id in enumerate(retrieved, start=1):
        if doc_id in relevant_set:
            return 1.0 / rank
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    """Normalized Discounted Cumulative Gain at rank k (binary relevance).

    Args:
        retrieved: Retrieved ids, best-first.
        relevant: Ground-truth relevant ids.
        k: Cutoff rank.

    Returns:
        NDCG@k in ``[0, 1]``.
    """
    relevant_set = set(relevant)
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, doc_id in enumerate(retrieved[:k], start=1)
        if doc_id in relevant_set
    )
    ideal_hits = min(len(relevant_set), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def column_f1(
    predicted: dict[str, str],
    expected: dict[str, str],
) -> dict[str, float]:
    """Column-level precision/recall/F1 for schema-mapping evaluation.

    Args:
        predicted: Map from source column to predicted target field.
        expected: Map from source column to ground-truth target field.

    Returns:
        Dict with ``precision``, ``recall``, ``f1``.
    """
    if not expected:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    correct = sum(
        1 for col, field in predicted.items() if expected.get(col) == field
    )
    precision = correct / len(predicted) if predicted else 0.0
    recall = correct / len(expected)
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall)
        else 0.0
    )
    return {
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }
