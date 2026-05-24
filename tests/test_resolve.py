"""Tests for the Step 3 entity-resolution helpers."""

from __future__ import annotations

import pandas as pd

from src.preprocess.resolve import (
    parse_model_code,
    resolve_models,
    resolve_parts,
    resolve_suppliers,
)


def test_parse_model_code_full() -> None:
    result = parse_model_code("WSED7667M.ABMQEUR")
    assert result is not None
    assert result.model_name == "WSED7667M"
    assert result.region == "EUR"
    assert result.grade_suffix == "ABMQ"


def test_parse_model_code_no_suffix() -> None:
    result = parse_model_code("WSED7667M")
    assert result is not None
    assert result.region is None
    assert result.grade_suffix is None


def test_parse_model_code_unknown_region_keeps_suffix() -> None:
    result = parse_model_code("WSED7667M.ABCDEF")
    assert result is not None
    assert result.region is None
    assert result.grade_suffix == "ABCDEF"


def test_parse_model_code_invalid_returns_none() -> None:
    assert parse_model_code("lowercase_garbage") is None


def test_resolve_parts_normalizes_canonical_form() -> None:
    df = pd.DataFrame({"base_part_no": ["ab 123-4567", " AB1234567"]})
    out = resolve_parts(df)
    assert out["canonical_part_no"].tolist() == ["AB1234567", "AB1234567"]


def test_resolve_models_adds_split_columns() -> None:
    df = pd.DataFrame(
        {"model_code": ["WSED7667M.ABMQEUR", "WSED9999M", "garbage"]}
    )
    out = resolve_models(df)
    assert out["model_name"].iloc[0] == "WSED7667M"
    assert out["model_region"].iloc[0] == "EUR"
    assert out["model_name"].iloc[1] == "WSED9999M"
    assert pd.isna(out["model_name"].iloc[2])


def test_resolve_suppliers_clusters_variants() -> None:
    raw = ["LG Innotek", "lg innotek", "LG  innotek", "Samsung SDI"]
    mapping = resolve_suppliers(raw, threshold=0.85)
    # All three LG variants should collapse to one canonical key.
    canonicals = {mapping[v] for v in raw if v in mapping}
    assert len(canonicals) == 2
