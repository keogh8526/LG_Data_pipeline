"""Tests for the Step 0 inventory scanner."""

from __future__ import annotations

from pathlib import Path

from src.extract.inventory import build_inventory, scan_file


def test_build_inventory_finds_all_fixtures(fixture_workbooks: Path) -> None:
    df = build_inventory(fixture_workbooks)
    assert not df.empty
    assert df["file_path"].nunique() == 4


def test_inventory_records_sheet_shape(fixture_workbooks: Path) -> None:
    df = build_inventory(fixture_workbooks)
    v12 = df[df["file_name"] == "sample_v12.xlsx"]
    assert "History" in set(v12["sheet_name"])
    assert (v12["max_col"] > 0).all()


def test_form_version_guess(fixture_workbooks: Path) -> None:
    df = build_inventory(fixture_workbooks)
    guesses = df.drop_duplicates("file_path").set_index("file_name")[
        "form_version_guess"
    ]
    assert guesses["sample_v12.xlsx"] == "v1.2"
    assert guesses["sample_96col.xlsx"] == "96col"


def test_scan_missing_file_does_not_crash_build(tmp_path: Path) -> None:
    # An empty directory yields an empty inventory, not an error.
    df = build_inventory(tmp_path)
    assert df.empty


def test_name_hints_extracted(fixture_workbooks: Path) -> None:
    df = build_inventory(fixture_workbooks)
    v12 = df[df["file_name"] == "sample_v12.xlsx"].iloc[0]
    assert v12["sheet_model_hint"] == "WSED7667M"
