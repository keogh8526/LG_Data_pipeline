"""Tests for the Step 3 value normalizer and the 10 trap fixtures."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.preprocess.extract import extract_rows
from src.preprocess.normalize import (
    NormalizationResult,
    is_null,
    normalize_dataframe,
    normalize_field,
)
from tests.fixtures.edge_cases import VALUE_TRAPS, make_merged_formula_workbook


@pytest.mark.parametrize("case", VALUE_TRAPS, ids=lambda c: c.label)
def test_value_traps(case) -> None:
    result = normalize_field(case.raw, case.field)
    assert isinstance(result, NormalizationResult)
    assert result.success == case.expects_success, (
        f"{case.label}: success={result.success} reason={result.fail_reason}"
    )
    if case.expects_success:
        assert result.value == case.expected, (
            f"{case.label}: got {result.value!r}, expected {case.expected!r}"
        )


def test_is_null_recognizes_common_sentinels() -> None:
    assert is_null(None)
    assert is_null(float("nan"))
    assert is_null("")
    assert is_null("   ")
    assert is_null("N/A")
    assert not is_null("ok")
    assert not is_null(0)


def test_part_no_invalid_quarantined() -> None:
    # 3 digits — fails the part_no pattern. Steps succeed but post_validate fails.
    result = normalize_field("123", "base_part_no")
    assert not result.success
    assert "post_validate" in (result.fail_reason or "")


def test_change_point_truncation_policy() -> None:
    long = "x" * 3000
    result = normalize_field(long, "change_point")
    assert result.success
    assert len(result.value) == 2000


def test_normalize_dataframe_attaches_quarantine_reason() -> None:
    df = pd.DataFrame(
        {
            "base_part_no": ["AB1234567", "123"],
            "change_point": ["foo", "bar"],
        }
    )
    out, report = normalize_dataframe(df, run_id="run_test")
    assert "_quarantine_reason" in out.columns
    assert pd.isna(out["_quarantine_reason"].iloc[0])
    assert isinstance(out["_quarantine_reason"].iloc[1], str)
    assert report.failures and report.failures[0]["field"] == "base_part_no"


def test_normalize_dataframe_records_audit_trail() -> None:
    df = pd.DataFrame(
        {
            "base_part_no": ["ab1234567"],
            "change_point": ["내열\r\n보강"],
        }
    )
    _out, report = normalize_dataframe(df, run_id="run_test")
    assert report.audit is not None
    entries = report.audit.entries
    fields_touched = {e.field_name for e in entries}
    assert {"base_part_no", "change_point"} <= fields_touched
    # Audit notes record the chain of applied steps.
    for entry in entries:
        assert entry.note  # one or more comma-joined step names


def test_extract_handles_merged_and_formula_cells(tmp_path: Path) -> None:
    # The merged/formula fixture has its header at row 1; build a minimal rule
    # on the fly rather than reusing the 56col rule (which expects row 7 for
    # real LG data).
    import re as _re

    from src.preprocess.map import MappingRule

    workbook = make_merged_formula_workbook(tmp_path / "merged.xlsx")
    rule = MappingRule(
        form_version="merged_fixture",
        header_row=1,
        include_patterns=[_re.compile(r"Better.*")],
        exclude_patterns=[],
    )
    df = extract_rows(workbook, rule)
    # 3 data rows (B3:B4 merge → row 4 reads empty for Part Name).
    assert len(df) == 3
    assert df["Base P/No"].iloc[0] == "AB1234567"
    # Formula cell read as its cached value.
    assert df["Qty"].iloc[0] == 2


def test_extract_sheet_meta_pulls_model_code_from_meta_header() -> None:
    """Real-data meta-header pattern (Base model label + value cell)."""
    from src.preprocess.extract import extract_sheet_meta
    from src.utils.excel import SheetData

    sheet = SheetData(
        name="변경 부품 list(BO605G1S5)",
        rows=[
            ["New & Changing Part Development List"],
            ["Base model", None, None, None, None, None, "모델명(등급)", "WS7D7610B / Cc\n(호주)"],
            [None, None, None, None, None, None, "Buyer명", "LGEAP"],
            [None, None, None, None, None, None, "Brand", "LG"],
            [None, None, None, None, None, None, "Set P/No.", "TAP35707209"],
        ],
    )
    meta = extract_sheet_meta(sheet)
    assert meta["model_code"] == "WS7D7610B"
    assert meta["buyer"] == "LGEAP"
    assert meta["brand"] == "LG"
    assert meta["set_part_no"] == "TAP35707209"
