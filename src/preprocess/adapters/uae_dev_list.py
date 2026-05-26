"""v2.0 §8-5 — UAE 24인치 신규 개발리스트 (46/58 col) 어댑터.

헤더 구조: 행2~행4 멀티헤더 (행2: 대분류, 행3: Event/모델명, 행4: 개발 등급).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.preprocess.adapters.base import (
    ExtractedRow,
    is_blank_row,
    iter_data_rows,
    normalize_cell_text,
    parse_multi_header,
)
from src.preprocess.column_dict import ColumnDictionary, load_column_dictionary
from src.utils.excel import SheetData
from src.utils.logging import get_logger

log = get_logger(__name__)

# 실측 UAE 신규개발리스트: row 3에 헤더, row 5부터 데이터 (row 4 비어있음).
HEADER_ROWS = [3]
DATA_START_ROW = 5


def extract_uae_dev_list(
    file_path: Path,
    sheet: SheetData,
    file_meta: dict[str, Any] | None = None,
    cdict: ColumnDictionary | None = None,
) -> Iterable[ExtractedRow]:
    cdict = cdict or load_column_dictionary()
    file_meta = file_meta or {}
    headers = parse_multi_header(sheet, HEADER_ROWS)
    if not headers:
        return

    rows_yielded = 0
    for row_idx, row in iter_data_rows(sheet, DATA_START_ROW):
        if is_blank_row(row):
            continue
        core: dict[str, Any] = {}
        payload: dict[str, Any] = {}
        semantic: dict[str, str] = {}
        for col_idx in range(1, len(row) + 1):
            header = headers.get(col_idx)
            if not header:
                continue
            value = row[col_idx - 1]
            payload[header] = value
            core_field = cdict.lookup(header)
            if core_field and value not in (None, ""):
                core[core_field] = cdict.map_cell_value(core_field, value)
                if cdict.is_semantic(header):
                    semantic[header] = normalize_cell_text(value)
        # Pydantic 필수 필드 fallback
        if not core.get("grade"):
            core["grade"] = "unknown"
        if not core.get("new_model_code"):
            core["new_model_code"] = "UNKNOWN"

        source_meta = {
            "source_file": file_path.name,
            "source_sheet": sheet.name,
            "source_row": row_idx,
            "form_version": "UAE_신규개발_58",
            **file_meta,
        }
        yield ExtractedRow(
            core=core, payload=payload, semantic=semantic, source_meta=source_meta
        )
        rows_yielded += 1
    log.info("adapter.uae.extracted", file=file_path.name, rows=rows_yielded)
