"""Tests for the Step 1 form-version classifier."""

from __future__ import annotations

from pathlib import Path

from src.preprocess.classify import classify_dir, classify_form, load_signatures

# Synthetic fixture -> expected form version (ground truth).
_EXPECTED = {
    "sample_v12.xlsx": "v1_2",
    "sample_96col.xlsx": "96col",
    "sample_56col.xlsx": "56col",
    "sample_20col.xlsx": "20col",
}


def test_signatures_load() -> None:
    versions = load_signatures()
    assert set(versions) == {"v1_2", "96col", "56col", "20col", "bom_tree"}


def test_all_fixtures_classified_correctly(fixture_workbooks: Path) -> None:
    for result in classify_dir(fixture_workbooks):
        name = Path(result.file_path).name
        assert result.form_version == _EXPECTED[name], name
        assert not result.needs_review, name
        assert result.confidence >= 0.85, name


def test_unknown_file_falls_back(tmp_path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["x"] * 40)  # 40 cols — matches no version range
    path = tmp_path / "weird.xlsx"
    wb.save(path)

    result = classify_form(path)
    assert result.form_version == "unknown"
    assert result.confidence == 0.0


def test_evidence_records_all_version_scores(fixture_workbooks: Path) -> None:
    result = classify_form(fixture_workbooks / "sample_96col.xlsx")
    assert set(result.evidence) == {"v1_2", "96col", "56col", "20col", "bom_tree"}
    assert result.evidence["96col"] >= 0.7


def test_sheet_results_present_for_multi_sheet_workbook(fixture_workbooks: Path) -> None:
    result = classify_form(fixture_workbooks / "sample_v12.xlsx")
    # Multi-sheet: file-level still resolves to v1_2 (sheet_exists fires),
    # but per-sheet scoring excludes file-level signals.
    assert result.form_version == "v1_2"
    assert set(result.sheet_results) == {"변경점List", "History"}
    main = result.sheet_results["변경점List"]
    # Without the History bonus, the main sheet alone falls short of the
    # threshold but still has a positive v1_2 score from its own header text.
    assert main.evidence["v1_2"] > 0


def test_sheet_results_for_single_sheet_fixtures(fixture_workbooks: Path) -> None:
    # Single-sheet workbooks: sheet-level decision agrees with file-level.
    result = classify_form(fixture_workbooks / "sample_96col.xlsx")
    assert len(result.sheet_results) == 1
    only = next(iter(result.sheet_results.values()))
    assert only.form_version == "96col"
    assert only.form_version == result.form_version
