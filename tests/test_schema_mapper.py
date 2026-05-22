"""Tests for the Step 3 schema mapper."""

from __future__ import annotations

import pandas as pd

from src.transform.schema_mapper import (
    FIXED_TARGET_FIELDS,
    apply_mapping,
    check_fixed_coverage,
    generate_rules_with_llm,
    load_rules,
    rules_for_version,
)


def test_load_all_rule_files() -> None:
    for version in ("20col", "56col", "96col"):
        rules = load_rules(rules_for_version(version))
        assert rules.form_version == version
        assert rules.mappings


def test_96col_covers_fixed_features() -> None:
    rules = load_rules(rules_for_version("96col"))
    assert check_fixed_coverage(rules) == set()


def test_apply_mapping_transforms_values() -> None:
    rules = load_rules(rules_for_version("96col"))
    source = pd.DataFrame(
        {
            "Base P/No": ["ab-123 4567"],
            "New P/No": ["AB1234568"],
            "BOM Level": ["2"],
            "Qty": ["3"],
            "구분": [" Change "],
        }
    )
    out = apply_mapping(source, rules)
    assert out.loc[0, "base_part_no"] == "AB1234567"
    assert out.loc[0, "bom_level"] == 2
    assert out.loc[0, "qty"] == 3.0
    assert out.loc[0, "change_type"] == "Change"


def test_apply_mapping_skips_missing_source_col() -> None:
    rules = load_rules(rules_for_version("96col"))
    out = apply_mapping(pd.DataFrame({"New P/No": ["AB1234568"]}), rules)
    assert "new_part_no" in out.columns
    assert "base_part_no" not in out.columns


def test_llm_fallback_matches_known_headers() -> None:
    rules = generate_rules_with_llm(
        "96col", ["Base P/No", "변경점", "unmappable column"]
    )
    targets = {m.target_field for m in rules.mappings}
    assert targets == {"base_part_no", "change_point"}
    assert all(m.confidence < 0.85 for m in rules.mappings)


def test_fixed_target_fields_count() -> None:
    assert len(FIXED_TARGET_FIELDS) == 10
