"""Tests for the Step 4 golden-diff comparator."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.preprocess.diff import diff_against_golden, load_golden


def _row(base: str, name: str = "Bracket", qty: float = 1.0) -> dict[str, object]:
    return {"base_part_no": base, "part_name": name, "qty": qty}


def test_identical_dataframes_match_fully() -> None:
    df = pd.DataFrame([_row("AB1234567"), _row("AB1234568")])
    report = diff_against_golden(df.copy(), df.copy())
    assert report.match_rate == 1.0
    assert report.rows_match == 2
    assert report.rows_mismatch == 0
    assert report.column_mismatches == {}


def test_column_difference_recorded_with_sample() -> None:
    auto = pd.DataFrame([_row("AB1234567", name="Bracket")])
    golden = pd.DataFrame([_row("AB1234567", name="BracketX")])
    report = diff_against_golden(auto, golden)
    assert report.rows_match == 0
    assert report.rows_mismatch == 1
    assert report.column_mismatches == {"part_name": 1}
    assert report.sample_mismatches and report.sample_mismatches[0]["base_part_no"] == "AB1234567"


def test_outer_join_counts_one_sided_rows() -> None:
    auto = pd.DataFrame([_row("AB1234567"), _row("AB1234568")])
    golden = pd.DataFrame([_row("AB1234567"), _row("AB1234569")])
    report = diff_against_golden(auto, golden)
    assert report.rows_only_in_auto == 1
    assert report.rows_only_in_golden == 1
    assert report.rows_match == 1


def test_whitespace_difference_does_not_flip_match() -> None:
    auto = pd.DataFrame([_row("AB1234567", name="Bracket ")])
    golden = pd.DataFrame([_row("AB1234567", name="Bracket")])
    report = diff_against_golden(auto, golden)
    assert report.rows_match == 1


def test_missing_key_raises() -> None:
    with pytest.raises(KeyError):
        diff_against_golden(pd.DataFrame({"x": [1]}), pd.DataFrame({"y": [1]}))


def test_load_golden_picks_parquet_if_present(tmp_path: Path) -> None:
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    df = pd.DataFrame([_row("AB1234567")])
    df.to_parquet(golden_dir / "raw.parquet")
    assert load_golden(golden_dir, Path("raw.xlsx")) is not None
    assert load_golden(golden_dir, Path("missing.xlsx")) is None
