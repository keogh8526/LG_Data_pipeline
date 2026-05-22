"""Tests for the Step 7 embedding and Neo4j hybrid-search helpers."""

from __future__ import annotations

import pytest

from src.embed.embedder import embed_texts, embedding_enabled
from src.embed.search import SearchHit, apply_version_weight


def _hits() -> list[SearchHit]:
    return [
        SearchHit(event_id="a", score=0.5, form_version="v1.2"),
        SearchHit(event_id="b", score=0.5, form_version="96col"),
    ]


def test_version_weight_boosts_preferred() -> None:
    rescored = apply_version_weight(_hits(), preferred_version="v1.2")
    assert rescored[0].event_id == "a"
    assert rescored[0].score > rescored[1].score


def test_version_weight_noop_when_none() -> None:
    hits = _hits()
    assert apply_version_weight(hits, preferred_version=None) == hits


def test_embedding_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_EMBEDDING", raising=False)
    assert not embedding_enabled()
    with pytest.raises(RuntimeError, match="ENABLE_EMBEDDING"):
        embed_texts(["x"])
