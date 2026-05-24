"""Tests for the Step 4 ValidationReport and the 15 metrics."""

from __future__ import annotations

import pandas as pd

from src.preprocess.validate import (
    EXPECTED_FIELDS,
    THRESHOLDS,
    ValidationReport,
    validate_dataframe,
)


def _clean_row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "base_part_no": "AB1234567",
        "new_part_no": "AB1234568",
        "part_name": "Bracket",
        "bom_level": 2,
        "part_type": "단품",
        "change_type": "Change",
        "change_point": "내열 보강",
        "change_reason": "필드 불량",
        "qty": 1.0,
        "model_code": "WSED7667M.ABMQEUR",
        "_quarantine_reason": None,
    }
    base.update(overrides)
    return base


def test_clean_dataframe_passes_all_gates() -> None:
    df = pd.DataFrame([_clean_row(), _clean_row(base_part_no="AB1234569")])
    report = validate_dataframe(df, run_id="r")
    assert report.is_acceptable(), report.critical_failures()
    assert report.column_match == 1.0
    assert report.value_format_match == 1.0
    assert report.referential_integrity == 1.0
    assert report.null_rate_required == 0.0
    assert report.axiom_violation_rate == 0.0


def test_bad_part_no_fails_value_format_match() -> None:
    df = pd.DataFrame(
        [
            _clean_row(base_part_no="123"),
            _clean_row(base_part_no="AB1234569"),
        ]
    )
    report = validate_dataframe(df, run_id="r")
    assert report.value_format_match < THRESHOLDS["value_format_match"]
    assert "value_format_match" in report.critical_failures()


def test_quarantine_reasons_drive_drop_reasons() -> None:
    df = pd.DataFrame(
        [
            _clean_row(),
            _clean_row(
                base_part_no="123",
                _quarantine_reason="base_part_no: post_validate failed",
            ),
        ]
    )
    report = validate_dataframe(df, run_id="r", rows_in=2)
    assert report.rows_quarantined == 1
    assert report.rows_out == 1
    assert report.drop_reasons.get("base_part_no") == 1


def test_missing_required_field_fails_null_rate_required() -> None:
    df = pd.DataFrame([_clean_row(base_part_no=None) for _ in range(10)])
    report = validate_dataframe(df, run_id="r")
    assert report.null_rate_required > THRESHOLDS["null_rate_required_max"]
    assert "null_rate_required" in report.critical_failures()


def test_outlier_rate_picks_up_qty_out_of_range() -> None:
    df = pd.DataFrame(
        [_clean_row(qty=99999.0), _clean_row(qty=1.0), _clean_row(qty=2.0)]
    )
    report = validate_dataframe(df, run_id="r")
    assert report.outlier_rate > 0


def test_report_serializes_to_pydantic() -> None:
    report = ValidationReport(run_id="r")
    data = report.model_dump(mode="json")
    assert data["run_id"] == "r"
    # All 15 metrics + meta should be present.
    assert {"column_match", "rows_in", "drop_reasons"} <= set(data)


def test_expected_fields_match_documented_ten() -> None:
    assert len(EXPECTED_FIELDS) == 10
