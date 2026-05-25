"""Tests for the Step 4 quarantine system."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd

from src.preprocess.quarantine import (
    extract_quarantined,
    list_quarantined,
    save_quarantine,
)


def _df_with_reasons() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "base_part_no": "AB1234567",
                "part_name": "Bracket",
                "_quarantine_reason": None,
                "_source_sheet": "Best-1",
            },
            {
                "base_part_no": "123",
                "part_name": "Cover",
                "_quarantine_reason": "base_part_no: post_validate failed",
                "_source_sheet": "Best-1",
            },
            {
                "base_part_no": None,
                "part_name": "Plate",
                "_quarantine_reason": "base_part_no: required empty",
                "_source_sheet": "Best-2",
            },
        ]
    )


def test_extract_quarantined_returns_only_failing_rows() -> None:
    records = extract_quarantined(_df_with_reasons(), "run_x", "raw.xlsx")
    assert len(records) == 2
    severities = {r.severity for r in records}
    assert severities == {"warning", "error"}
    assert records[1].source_sheet == "Best-2"


def test_extract_quarantined_empty_when_no_failures() -> None:
    df = pd.DataFrame([{"base_part_no": "AB1234567", "_quarantine_reason": None}])
    assert extract_quarantined(df, "run_x", "raw.xlsx") == []


def test_save_and_list_round_trip(tmp_path: Path) -> None:
    records = extract_quarantined(_df_with_reasons(), "run_x", "raw.xlsx")
    with patch("src.preprocess.quarantine.QUARANTINE_DIR", tmp_path):
        save_quarantine(records, "run_x", "raw")
        rows = list_quarantined("run_x")
    assert len(rows) == 2
    assert rows[0]["run_id"] == "run_x"
    assert rows[0]["stage_failed"] == "base_part_no"


def test_save_quarantine_no_records_returns_none(tmp_path: Path) -> None:
    with patch("src.preprocess.quarantine.QUARANTINE_DIR", tmp_path):
        assert save_quarantine([], "run_x", "raw") is None
