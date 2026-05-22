"""Tests for the Step 2 form classifier."""

from __future__ import annotations

from pathlib import Path

from src.extract.form_classifier import classify_form


def test_classify_v12(fixture_workbooks: Path) -> None:
    result = classify_form(fixture_workbooks / "sample_v12.xlsx")
    assert result.form_version == "v1.2"
    assert result.confidence > 0.8


def test_classify_96col(fixture_workbooks: Path) -> None:
    result = classify_form(fixture_workbooks / "sample_96col.xlsx")
    assert result.form_version == "96col"


def test_classify_56col(fixture_workbooks: Path) -> None:
    result = classify_form(fixture_workbooks / "sample_56col.xlsx")
    assert result.form_version == "56col"


def test_classify_20col(fixture_workbooks: Path) -> None:
    result = classify_form(fixture_workbooks / "sample_20col.xlsx")
    assert result.form_version == "20col"


def test_unknown_has_zero_confidence(tmp_path: Path) -> None:
    import openpyxl

    # 78 columns, no History/Better sheet, no aaaa/stage markers -> no rule matches.
    wb = openpyxl.Workbook()
    wb.active.append([f"col{i}" for i in range(78)])
    path = tmp_path / "weird.xlsx"
    wb.save(path)
    result = classify_form(path)
    assert result.form_version == "unknown"
    assert result.confidence == 0.0
