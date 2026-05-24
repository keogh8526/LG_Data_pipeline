"""Tests for the Step 1 form-version classifier."""

from __future__ import annotations

from pathlib import Path

from src.preprocess.classify import classify_dir, classify_form, load_signatures

# Synthetic fixture -> expected form version (ground truth).
_EXPECTED = {
    "sample_v12.xlsx": "v1_2",
    "sample_96col.xlsx": "v96col",
    "sample_56col.xlsx": "v56col",
    "sample_20col.xlsx": "v20col",
}


def test_signatures_load() -> None:
    versions = load_signatures()
    assert set(versions) == {"v1_2", "v96col", "v56col", "v20col"}


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
    assert set(result.evidence) == {"v1_2", "v96col", "v56col", "v20col"}
    assert result.evidence["v96col"] >= 0.7
