"""Tests for the Step 0 raw-file inventory scanner."""

from __future__ import annotations

from pathlib import Path

from src.preprocess.inventory import build_inventory, guess_form_version, scan_file


def test_inventory_scans_all_fixtures(fixture_workbooks: Path) -> None:
    df = build_inventory(fixture_workbooks)
    assert not df.empty
    # Four fixture workbooks (the v1.2 file has a second History sheet).
    assert df["file_path"].nunique() == 4
    expected = {
        "file_path",
        "file_name",
        "sheet_name",
        "max_row",
        "max_col",
        "form_version_guess",
    }
    assert expected <= set(df.columns)


def test_inventory_empty_dir(tmp_path: Path) -> None:
    assert build_inventory(tmp_path).empty


def test_scan_file_reads_sheets(fixture_workbooks: Path) -> None:
    info = scan_file(fixture_workbooks / "sample_96col.xlsx")
    assert info.sheet_count >= 1
    assert info.sheets[0].max_col >= 90


def test_form_version_guess_is_coarse(fixture_workbooks: Path) -> None:
    info = scan_file(fixture_workbooks / "sample_96col.xlsx")
    assert guess_form_version(info) == "96col"
