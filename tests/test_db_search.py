"""Tests for Step 5 — hybrid search SQL structure (Postgres-only execution).

We assert that the rendered SQL contains the pgvector cast, the pg_trgm
similarity operator, and the weighted-sum scoring. Real execution requires a
Postgres instance and is exercised manually via ``python -m src.cli search``.
"""

from __future__ import annotations

from src.db.search import (
    CANDIDATE_LIMIT,
    HYBRID_SQL,
    LEXICAL_WEIGHT,
    SEMANTIC_WEIGHT,
)


def test_hybrid_sql_uses_pgvector_cast() -> None:
    assert "CAST(:emb AS vector)" in HYBRID_SQL


def test_hybrid_sql_uses_trgm_similarity() -> None:
    # `%%` is the SQLAlchemy-escaped `%` operator (pg_trgm).
    assert "similarity(change_point, :q)" in HYBRID_SQL
    assert "change_point %% :q" in HYBRID_SQL


def test_hybrid_sql_form_version_filter_present() -> None:
    assert ":form_ver IS NULL OR form_version = :form_ver" in HYBRID_SQL


def test_hybrid_sql_uses_weighted_fusion() -> None:
    assert f"* {SEMANTIC_WEIGHT}" in HYBRID_SQL
    assert f"* {LEXICAL_WEIGHT}" in HYBRID_SQL
    assert SEMANTIC_WEIGHT + LEXICAL_WEIGHT == 1.0


def test_candidate_limit_applied() -> None:
    assert f"LIMIT {CANDIDATE_LIMIT}" in HYBRID_SQL
