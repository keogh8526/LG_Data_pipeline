"""Tests for the Step 4 markdown report builder."""

from __future__ import annotations

from pathlib import Path

from src.preprocess.diff import DiffReport
from src.preprocess.report import build_markdown_report
from src.preprocess.validate import ValidationReport


def _passing_report(run_id: str = "run_t") -> ValidationReport:
    return ValidationReport(
        run_id=run_id,
        column_match=1.0,
        type_match=1.0,
        value_format_match=1.0,
        referential_integrity=1.0,
        row_preservation=1.0,
        null_rate_required=0.0,
        axiom_violation_rate=0.0,
        rows_in=10,
        rows_out=10,
    )


def test_report_contains_summary_metrics_and_verdict(tmp_path: Path) -> None:
    aggregate = _passing_report()
    file_validation = _passing_report()
    file_validation.file_path = "raw/foo.xlsx"
    file_validation.form_version = "96col"
    diff = DiffReport(rows_match=9, rows_mismatch=1, column_mismatches={"qty": 1})

    output = build_markdown_report(
        run_id="run_t",
        file_reports=[("raw/foo.xlsx", file_validation, diff)],
        aggregate=aggregate,
        output_dir=tmp_path,
    )
    text = output.read_text(encoding="utf-8")
    assert "# Preprocessing Report — run_t" in text
    assert "ACCEPTABLE" in text
    assert "## Aggregate Validation" in text
    assert "Golden diff:" in text
    assert "match_rate=0.900" in text


def test_report_marks_failures_in_decision_section(tmp_path: Path) -> None:
    aggregate = _passing_report()
    aggregate.value_format_match = 0.5  # below threshold
    aggregate.axiom_violation_rate = 0.5  # above threshold
    output = build_markdown_report(
        run_id="run_t",
        file_reports=[],
        aggregate=aggregate,
        output_dir=tmp_path,
    )
    text = output.read_text(encoding="utf-8")
    assert "NOT ACCEPTABLE" in text
    assert "value_format_match" in text
    assert "axiom_violation_rate" in text
