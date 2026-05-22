"""Tests for the Step 7 hybrid-search helpers."""

from __future__ import annotations

import pytest

from src.embed.embedder import embed_texts, embedding_enabled
from src.embed.search import apply_version_weight, reciprocal_rank_fusion


def test_rrf_fuses_rankings() -> None:
    fused = reciprocal_rank_fusion(["a", "b", "c"], ["b", "a", "d"])
    ids = [doc_id for doc_id, _ in fused]
    # 'a' and 'b' appear in both lists -> ranked above singletons.
    assert set(ids[:2]) == {"a", "b"}


def test_version_weight_boosts_preferred() -> None:
    hits = [("a", 0.5), ("b", 0.5)]
    versions = {"a": "v1.2", "b": "96col"}
    rescored = dict(apply_version_weight(hits, versions, "v1.2"))
    assert rescored["a"] > rescored["b"]


def test_version_weight_noop_when_none() -> None:
    hits = [("a", 0.5)]
    assert apply_version_weight(hits, {"a": "v1.2"}, None) == hits


def test_embedding_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_EMBEDDING", raising=False)
    assert not embedding_enabled()
    with pytest.raises(RuntimeError, match="ENABLE_EMBEDDING"):
        embed_texts(["x"])
