"""Tests for the Step 3 mapping module (per-form -> 96col)."""

from __future__ import annotations

import pandas as pd

from src.preprocess.map import apply_mapping, load_mapping_rule, sheet_passes


def test_load_all_rules() -> None:
    for form in ["20col", "56col", "96col", "v1_2"]:
        rule = load_mapping_rule(form)
        assert rule.form_version == form
        assert rule.mappings  # at least one target

        # Every rule maps the same 10 fixed features.
        expected = {
            "base_part_no",
            "new_part_no",
            "part_name",
            "bom_level",
            "part_type",
            "change_type",
            "change_point",
            "change_reason",
            "qty",
            "model_code",
        }
        assert expected <= set(rule.mappings)


def test_sheet_passes_filters() -> None:
    rule = load_mapping_rule("56col")
    assert sheet_passes("Better-1", rule.include_patterns, rule.exclude_patterns)
    assert sheet_passes("Best-1", rule.include_patterns, rule.exclude_patterns)
    assert not sheet_passes(
        "History", rule.include_patterns, rule.exclude_patterns
    )


def test_apply_mapping_picks_source_by_priority() -> None:
    # 20col base_part_no sources: "P/no." (1), "Base P/No" (2).
    rule = load_mapping_rule("20col")
    df = pd.DataFrame(
        {
            "P/no.": ["ab1234567 "],
            "Base P/No": ["IGNORED"],
            "Part Name": ["Bracket"],
        }
    )
    mapped = apply_mapping(df, rule)
    assert mapped["base_part_no"].iloc[0] == "AB1234567"
    assert mapped["part_name"].iloc[0] == "Bracket"


def test_apply_mapping_records_quarantine_for_missing_required() -> None:
    rule = load_mapping_rule("96col")
    df = pd.DataFrame({"Class Desc.": ["Bracket"]})  # missing required base/model
    mapped = apply_mapping(df, rule)
    reason = mapped["_quarantine_reason"].iloc[0]
    assert reason is not None
    assert "base_part_no" in reason
    assert "model_code" in reason


def test_change_type_alias_applied_during_mapping() -> None:
    rule = load_mapping_rule("96col")
    df = pd.DataFrame(
        {
            "Base P/No": ["AB1234567"],
            "New P/No": ["AB1234568"],
            "Class Desc.": ["Bracket"],
            "BOM Level": [2],
            "Part Type": ["단품"],
            "구분": ["신규"],
            "변경점": ["내열 보강"],
            "Model Code": ["WSED7667M.ABMQEUR"],
        }
    )
    mapped = apply_mapping(df, rule)
    assert mapped["change_type"].iloc[0] == "New"
