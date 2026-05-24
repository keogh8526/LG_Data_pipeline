"""Tests for the Step 3 preprocessing pipeline orchestration."""

from __future__ import annotations

from pathlib import Path

from src.preprocess.pipeline import (
    generate_run_id,
    preprocess_directory,
    preprocess_file,
)


def test_run_id_is_unique_and_well_formed() -> None:
    a = generate_run_id()
    b = generate_run_id()
    assert a != b
    assert a.startswith("run_")


def test_preprocess_file_on_v12_fixture(fixture_workbooks: Path) -> None:
    result = preprocess_file(
        fixture_workbooks / "sample_v12.xlsx", run_id="run_test"
    )
    assert result.status == "ok"
    assert result.form_version == "v1_2"
    assert result.df is not None
    assert {"source_file", "form_version", "run_id"} <= set(result.df.columns)
    # The v12 fixture's one data row maps cleanly (no quarantine reason).
    clean_rows = result.df["_quarantine_reason"].isna().sum()
    assert clean_rows >= 1


def test_preprocess_directory_runs_every_fixture(fixture_workbooks: Path) -> None:
    summary = preprocess_directory(fixture_workbooks, run_id="run_test")
    statuses = {r.status for r in summary.results}
    # Only "ok", "empty", or "needs_human_classification" are expected here.
    assert statuses <= {"ok", "empty", "needs_human_classification"}
    assert len(summary.results) == 4


def test_preprocess_unknown_file_short_circuits(tmp_path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["x"] * 40)
    path = tmp_path / "weird.xlsx"
    wb.save(path)

    result = preprocess_file(path, run_id="run_test")
    assert result.status == "needs_human_classification"
