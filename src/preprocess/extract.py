"""Step 3 — row extraction from raw Excel files (per-form).

Uses ``MappingRule.header_row`` and ``sheet_filter`` to read every relevant
sheet of a workbook and concatenate the data rows into one DataFrame. The
source sheet name is preserved as ``_source_sheet`` for downstream provenance.

Excel-cell quirks (merged cells expanded by the writer, formulas resolved to
their cached value) are handled by ``src.utils.excel.read_workbook`` which
prefers openpyxl and falls back to python-calamine for malformed workbooks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from src.preprocess.map import MappingRule, sheet_passes
from src.utils.excel import SheetData, read_workbook
from src.utils.logging import get_logger

log = get_logger(__name__)

_HEADER_WS_RE = re.compile(r"\s+")


def list_sheets(file_path: Path, rule: MappingRule) -> list[str]:
    """Return the sheet names of ``file_path`` that pass the rule's filter.

    Args:
        file_path: Path to the workbook.
        rule: The mapping rule (uses ``include_patterns`` / ``exclude_patterns``).

    Returns:
        The matched sheet names in workbook order.
    """
    return [
        sheet.name
        for sheet in read_workbook(file_path)
        if sheet_passes(sheet.name, rule.include_patterns, rule.exclude_patterns)
    ]


def _normalize_header(value: object, index: int) -> str:
    """Collapse whitespace in a header cell; placeholder for empty cells."""
    if value is None or value == "":
        return f"Unnamed: {index}"
    return _HEADER_WS_RE.sub(" ", str(value)).strip()


def _sheet_to_dataframe(sheet: SheetData, header_row: int) -> pd.DataFrame:
    """Slice raw rows into a DataFrame using the configured 1-indexed header.

    Header cells with embedded newlines (common in 96col multi-line headers
    like ``"BOM\\nLevel"``) are whitespace-collapsed so mapping rules can use
    natural column names.
    """
    if len(sheet.rows) < header_row:
        return pd.DataFrame()
    header_values = sheet.rows[header_row - 1]
    columns = [_normalize_header(v, i) for i, v in enumerate(header_values)]
    body = sheet.rows[header_row:]
    if not body:
        return pd.DataFrame(columns=columns)
    # Pad / trim each row to the header width so pandas accepts the matrix.
    width = len(columns)
    normalized: list[list[object]] = []
    for row in body:
        if len(row) < width:
            row = list(row) + [None] * (width - len(row))
        elif len(row) > width:
            row = list(row[:width])
        else:
            row = list(row)
        normalized.append(row)
    df = pd.DataFrame(normalized, columns=columns)
    return df.dropna(how="all").reset_index(drop=True)


def extract_rows(file_path: Path, rule: MappingRule) -> pd.DataFrame:
    """Read all data rows from sheets matching the rule, using its header row.

    Args:
        file_path: Path to the workbook.
        rule: Parsed mapping rule.

    Returns:
        A single DataFrame concatenating all matched sheets. Empty if no sheet
        passes the filter or all matched sheets are header-only.
    """
    sheets = read_workbook(file_path)
    parts: list[pd.DataFrame] = []
    for sheet in sheets:
        if not sheet_passes(sheet.name, rule.include_patterns, rule.exclude_patterns):
            continue
        df = _sheet_to_dataframe(sheet, rule.header_row)
        if df.empty:
            continue
        df["_source_sheet"] = sheet.name
        parts.append(df)

    if not parts:
        log.info("extract.no_matching_sheets", file=file_path.name)
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
