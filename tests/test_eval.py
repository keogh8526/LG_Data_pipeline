"""Tests for the Step 8 evaluation infrastructure."""

from __future__ import annotations

from src.eval.metrics import (
    column_f1,
    mean_reciprocal_rank,
    ndcg_at_k,
    recall_at_k,
)
from src.eval.runner import evaluate_mapping, evaluate_retrieval, load_jsonl
from src.utils.paths import EVAL_DIR


def test_recall_at_k() -> None:
    assert recall_at_k(["a", "b", "c"], ["b", "d"], k=3) == 0.5
    assert recall_at_k(["a"], [], k=3) == 1.0


def test_mrr() -> None:
    assert mean_reciprocal_rank(["a", "b"], ["b"]) == 0.5
    assert mean_reciprocal_rank(["a"], ["z"]) == 0.0


def test_ndcg_perfect_ranking() -> None:
    assert ndcg_at_k(["a", "b"], ["a", "b"], k=2) == 1.0


def test_column_f1() -> None:
    scores = column_f1({"X": "f1", "Y": "f2"}, {"X": "f1", "Y": "f9"})
    assert scores["precision"] == 0.5
    assert scores["recall"] == 0.5


def test_eval_datasets_load() -> None:
    retrieval = load_jsonl(EVAL_DIR / "retrieval_eval.jsonl")
    mapping = load_jsonl(EVAL_DIR / "mapping_eval.jsonl")
    assert retrieval and mapping
    assert "query" in retrieval[0]


def test_evaluate_retrieval_with_stub() -> None:
    dataset = load_jsonl(EVAL_DIR / "retrieval_eval.jsonl")
    metrics = evaluate_retrieval(dataset, retrieve=lambda _q: ["EVT-0001"], k=10)
    assert metrics["n"] == len(dataset)
    assert 0.0 <= metrics["recall@k"] <= 1.0


def test_evaluate_mapping_with_stub() -> None:
    dataset = load_jsonl(EVAL_DIR / "mapping_eval.jsonl")
    metrics = evaluate_mapping(
        dataset, map_fn=lambda _f: {"Base P/No": "base_part_no"}
    )
    assert metrics["n"] == len(dataset)
