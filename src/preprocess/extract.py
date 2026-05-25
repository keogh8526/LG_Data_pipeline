"""Step 3 — row extraction from raw Excel files (per-form).

Uses ``MappingRule.header_row`` and ``sheet_filter`` to read every relevant
sheet of a workbook and concatenate the data rows into one DataFrame. The
source sheet name is preserved as ``_source_sheet`` for downstream provenance,
and label/value pairs found in the meta header rows above the data block (the
``Base model`` / ``Buyer명`` / ``Set P/No.`` block common to 20col, 56col, and
96col masters) are extracted via :func:`extract_sheet_meta` and broadcast as
``_meta_*`` columns so mapping rules can fall back to them when the data rows
themselves omit, for instance, ``model_code``.

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

# How deep to scan for label→value meta pairs above the data block.
_META_SCAN_ROWS = 8
_META_VALUE_LOOKAHEAD = 10  # cells to the right of a label

# Label → meta key. Labels are matched case-insensitively after whitespace
# collapse, so ``Base Model/Suffix`` matches ``base model/suffix`` etc.
_META_LABEL_MAP: dict[str, str] = {
    "base model": "model_code",
    "base model/suffix": "model_code",
    "base model / suffix": "model_code",
    "buyer명": "buyer",
    "brand": "brand",
    "set p/no.": "set_part_no",
    "set p/no": "set_part_no",
    "양산 일자": "production_date",
}

# Cells that look like labels rather than values; skip past them when hunting
# for the actual value cell on the right of a ``_META_LABEL_MAP`` hit.
_META_SKIP_LABELS: frozenset[str] = frozenset(
    {
        "모델명(등급)",
        "model name(grade)",
        "개발 모델",
        "new model",
        "event",
    }
)


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


def _clean_model_code(value: str) -> str:
    """Take the model-code token from a meta cell like ``WS7D7610B / Cc\\n(호주)``."""
    head = re.split(r"[/(\n]", value, maxsplit=1)[0]
    return _HEADER_WS_RE.sub("", head).upper()


def extract_sheet_meta(sheet: SheetData) -> dict[str, str]:
    """Pull label→value pairs from the meta header rows above the data block.

    Args:
        sheet: A sheet read via :func:`src.utils.excel.read_workbook`.

    Returns:
        A dict keyed by canonical names (``model_code``, ``buyer``, ``brand``,
        ``set_part_no``, ``production_date``). Returns an empty dict when no
        recognized label is found.
    """
    meta: dict[str, str] = {}
    for row in sheet.rows[:_META_SCAN_ROWS]:
        for col_idx, cell in enumerate(row):
            if cell is None or cell == "":
                continue
            label = _HEADER_WS_RE.sub(" ", str(cell)).strip().lower()
            target = _META_LABEL_MAP.get(label)
            if target is None or target in meta:
                continue
            for right_idx in range(
                col_idx + 1, min(col_idx + 1 + _META_VALUE_LOOKAHEAD, len(row))
            ):
                value = row[right_idx]
                if value is None or value == "":
                    continue
                text = _HEADER_WS_RE.sub(" ", str(value)).strip()
                # Skip cells that are themselves labels (real 96col/20col
                # workbooks chain a label like ``모델명(등급)`` between the
                # outer label and the actual value).
                if (
                    text in _META_SKIP_LABELS
                    or text.lower() in _META_LABEL_MAP
                ):
                    continue
                cleaned = (
                    _clean_model_code(text)
                    if target == "model_code"
                    else text
                )
                if cleaned:
                    meta[target] = cleaned
                break
    return meta


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
        # Broadcast meta-header label/value pairs as ``_meta_*`` columns so
        # mapping rules can fall back to them when data rows omit the value.
        meta = extract_sheet_meta(sheet)
        for key, value in meta.items():
            df[f"_meta_{key}"] = value
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
