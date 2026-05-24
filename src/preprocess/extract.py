"""Step 3 — row extraction from raw Excel files (per-form).

Uses ``MappingRule.header_row`` and ``sheet_filter`` to read every relevant
sheet of a workbook and concatenate the data rows into one DataFrame. The
source sheet name is preserved as ``_source_sheet`` for downstream provenance.

Excel-cell quirks (merged cells expanded by the writer, formulas resolved to
their cached value) are handled by pandas + openpyxl with ``data_only=True``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.preprocess.map import MappingRule, sheet_passes
from src.utils.logging import get_logger

log = get_logger(__name__)


def list_sheets(file_path: Path, rule: MappingRule) -> list[str]:
    """Return the sheet names of ``file_path`` that pass the rule's filter.

    Args:
        file_path: Path to the workbook.
        rule: The mapping rule (uses ``include_patterns`` / ``exclude_patterns``).

    Returns:
        The matched sheet names in workbook order.
    """
    xl = pd.ExcelFile(file_path)
    return [
        name
        for name in xl.sheet_names
        if sheet_passes(name, rule.include_patterns, rule.exclude_patterns)
    ]


def extract_rows(file_path: Path, rule: MappingRule) -> pd.DataFrame:
    """Read all data rows from sheets matching the rule, using its header row.

    Args:
        file_path: Path to the workbook.
        rule: Parsed mapping rule.

    Returns:
        A single DataFrame concatenating all matched sheets. Empty if no sheet
        passes the filter or all matched sheets are header-only.
    """
    matched = list_sheets(file_path, rule)
    if not matched:
        log.info("extract.no_matching_sheets", file=file_path.name)
        return pd.DataFrame()

    parts: list[pd.DataFrame] = []
    for sheet in matched:
        df = pd.read_excel(
            file_path,
            sheet_name=sheet,
            header=rule.header_row - 1,
            engine="openpyxl",
        )
        # Drop rows that are entirely empty (common after the data block ends).
        df = df.dropna(how="all").reset_index(drop=True)
        if df.empty:
            continue
        df["_source_sheet"] = sheet
        parts.append(df)

    if not parts:
        return pd.DataFrame()
    out = pd.concat(parts, ignore_index=True)
    log.info(
        "extract.rows",
        file=file_path.name,
        sheets=len(parts),
        rows=len(out),
        cols=len(out.columns),
    )
    return out
