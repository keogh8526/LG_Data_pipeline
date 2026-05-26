"""v2.0 §8-2 — 신규부품리스트 (75col, BO24 패밀리) 어댑터.

실측 신규부품리스트: row 1=Key-In/LOV, row 2=필수/옵션, row 3=실 헤더, row 4 빈,
데이터 row 5+. row 3만 사용해 leaf 헤더 path 생성.

D-011: 담당자 15회 슬롯 직렬화(_collect_role_slots) 제거. BOM Agent 답변에
담당자 정보가 안 쓰임. 담당자 컬럼은 extra_fields에 그대로 보존 (필요 시
사람이 직접 조회).
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

HEADER_ROWS = [3]
DATA_START_ROW = 5


def extract_new_parts_list_75(
    file_path: Path,
    sheet: SheetData,
    file_meta: dict[str, Any] | None = None,
    cdict: ColumnDictionary | None = None,
) -> Iterable[ExtractedRow]:
    cdict = cdict or load_column_dictionary()
    file_meta = file_meta or {}
    headers = parse_multi_header(sheet, HEADER_ROWS)

    if not headers:
        log.warning("adapter.new_parts.no_headers", file=file_path.name, sheet=sheet.name)
        return

    rows_yielded = 0
    for row_idx, row in iter_data_rows(sheet, DATA_START_ROW):
        if is_blank_row(row):
            continue
        core: dict[str, Any] = {}
        payload: dict[str, Any] = {}
        semantic: dict[str, str] = {}

        for col_idx in range(1, len(row) + 1):
            header_path = headers.get(col_idx)
            if not header_path:
                continue
            value = row[col_idx - 1]
            payload[header_path] = value

            core_field = cdict.lookup(header_path)
            if core_field and value not in (None, ""):
                core[core_field] = cdict.map_cell_value(core_field, value)
                if cdict.is_semantic(header_path):
                    semantic[header_path] = normalize_cell_text(value)

        # Pydantic 필수 필드 fallback
        if not core.get("grade"):
            core["grade"] = "unknown"
        if not core.get("new_model_code"):
            core["new_model_code"] = "UNKNOWN"

        source_meta = {
            "source_file": file_path.name,
            "source_sheet": sheet.name,
            "source_row": row_idx,
            "form_version": "신규부품리스트_75",
            **file_meta,
        }

        yield ExtractedRow(
            core=core,
            payload=payload,
            semantic=semantic,
            source_meta=source_meta,
        )
        rows_yielded += 1

    log.info(
        "adapter.new_parts.extracted",
        file=file_path.name,
        sheet=sheet.name,
        rows=rows_yielded,
    )
