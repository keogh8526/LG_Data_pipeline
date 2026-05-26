"""v2.0 §8-4 — base_master (24col, 구버전) 어댑터.

헤더 구조: 행2에 23개 컬럼명, 행4부터 데이터.

Core 매핑:
  P/no.           → core.part_no
  Desc.           → core.part_name
  Module          → payload
  CMDT            → payload (절삭 등)
  도입/신규        → core.change_type
  Lvl             → core.bom_level
  Part Grade       → core.grade
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.preprocess.adapters.base import (
    ExtractedRow,
    cell_at,
    is_blank_row,
    iter_data_rows,
    normalize_cell_text,
)
from src.preprocess.column_dict import ColumnDictionary, load_column_dictionary
from src.utils.excel import SheetData
from src.utils.logging import get_logger

log = get_logger(__name__)

# 실측 base_master.xlsx 분석: 헤더는 row 1, 데이터 row 3부터 (row 2 비어있음).
# 합성 fixture는 row 2 헤더 / row 4 데이터 — 시트의 row 1 첫 셀로 동적 판단.
HEADER_ROW = 1
DATA_START_ROW = 3


def extract_base_master_24(
    file_path: Path,
    sheet: SheetData,
    file_meta: dict[str, Any] | None = None,
    cdict: ColumnDictionary | None = None,
) -> Iterable[ExtractedRow]:
    cdict = cdict or load_column_dictionary()
    file_meta = file_meta or {}

    # 동적 헤더 행 탐색 — row 1~3 중 'No.' 가 첫 번째 등장한 행이 헤더.
    header_row = HEADER_ROW
    data_start = DATA_START_ROW
    for r in range(1, 4):
        first_cell = normalize_cell_text(cell_at(sheet.rows, r, 2))
        if first_cell in {"No.", "No", "no."}:
            header_row = r
            data_start = r + 2  # 빈 row 한 줄 skip
            break

    headers: dict[int, str] = {}
    for c in range(1, sheet.max_col + 1):
        h = normalize_cell_text(cell_at(sheet.rows, header_row, c))
        if h:
            headers[c] = h

    if not headers:
        log.warning("adapter.base_master.no_headers", file=file_path.name, sheet=sheet.name)
        return

    rows_yielded = 0
    for row_idx, row in iter_data_rows(sheet, data_start):
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

        # grade/new_model_code 누락 시 sentinel 채움 (Pydantic 필수 통과)
        if not core.get("grade"):
            core["grade"] = "unknown"
        if not core.get("new_model_code"):
            # base_master_24는 모델코드 컬럼 없음 — 파일명에서 추출 또는 sentinel
            core["new_model_code"] = "UNKNOWN"

        source_meta = {
            "source_file": file_path.name,
            "source_sheet": sheet.name,
            "source_row": row_idx,
            "form_version": "base_master_24",
            **file_meta,
        }
        yield ExtractedRow(
            core=core,
            payload=payload,
            semantic=semantic,
            source_meta=source_meta,
        )
        rows_yielded += 1
    log.info("adapter.base_master.extracted", file=file_path.name, rows=rows_yielded)
