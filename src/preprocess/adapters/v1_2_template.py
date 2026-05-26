"""v2.0 §8-3 — v1.2 통합 마스터 (빈 템플릿, 59col) 어댑터.

현재 빈 템플릿 상태. 실데이터 5건+ 누적 시 활성화. v2.0 MVP에서는
schema discovery만 하고 적재 skip (빈 행 yield 안 함).

진화 트리거: v1.2 채워진 파일이 5건 이상 도착하면 column_dictionary를
업데이트하고 본 어댑터를 changing_parts_list와 동일 매핑으로 활성화.
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

HEADER_ROWS = [1, 2, 3]
DATA_START_ROW = 4


def extract_v1_2_template(
    file_path: Path,
    sheet: SheetData,
    file_meta: dict[str, Any] | None = None,
    cdict: ColumnDictionary | None = None,
) -> Iterable[ExtractedRow]:
    cdict = cdict or load_column_dictionary()
    file_meta = file_meta or {}
    headers = parse_multi_header(sheet, HEADER_ROWS)

    # Discovery 로그 — 빈 템플릿이라도 헤더는 기록해두면 column_dictionary 성장에 도움
    log.info(
        "adapter.v1_2.headers_discovered",
        file=file_path.name,
        sheet=sheet.name,
        header_count=len(headers),
    )

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
            "form_version": "v1_2_template_59",
            **file_meta,
        }
        yield ExtractedRow(
            core=core, payload=payload, semantic=semantic, source_meta=source_meta
        )
        rows_yielded += 1
    if rows_yielded:
        log.info("adapter.v1_2.extracted", file=file_path.name, rows=rows_yielded)
