"""Tests for the Step 4 entity resolution."""

from __future__ import annotations

from src.transform.entity_resolution import (
    parse_model_code,
    resolve_buyers,
    resolve_models,
    resolve_parts,
    resolve_suppliers,
    summarize,
)


def test_resolve_parts_dedups_by_normalized_form() -> None:
    df = resolve_parts(["AB1234567", "ab-123 4567", "CD7654321"])
    assert len(df) == 2
    ab_row = df[df["canonical_id"] == "AB1234567"].iloc[0]
    assert set(ab_row["alias"]) == {"AB1234567", "ab-123 4567"}


def test_resolve_parts_flags_invalid() -> None:
    df = resolve_parts(["123", "AB1234567"])
    invalid = df[df["canonical_id"] == "123"].iloc[0]
    assert invalid["invalid"]
    assert invalid["needs_review"]
    valid = df[df["canonical_id"] == "AB1234567"].iloc[0]
    assert not valid["invalid"]


def test_parse_model_code() -> None:
    parts = parse_model_code("WSED7667M.ABMQEUR")
    assert parts.model_name == "WSED7667M"
    assert parts.grade_suffix == "ABM"
    assert parts.region == "EUR"


def test_resolve_models() -> None:
    df = resolve_models(["WSED7667M.ABMQEUR", "wsed7667m.abmqeur"])
    assert len(df) == 1
    assert df.iloc[0]["region"] == "EUR"


def test_resolve_buyers_maps_region() -> None:
    df = resolve_buyers(["LGEUR", "LGXXX"])
    eur = df[df["canonical_id"] == "LGEUR"].iloc[0]
    assert eur["region"] == "Europe"
    unknown = df[df["canonical_id"] == "LGXXX"].iloc[0]
    assert unknown["needs_review"]


def test_resolve_suppliers_fuzzy_merge() -> None:
    df = resolve_suppliers(["ACME Corp", "ACME Corp.", "Globex"])
    assert df["canonical_id"].nunique() == 2


def test_summarize() -> None:
    df = resolve_parts(["AB1234567", "bad"])
    stats = summarize(df, "parts")
    assert stats["rows"] == 2
    assert 0.0 <= stats["invalid_ratio"] <= 1.0
