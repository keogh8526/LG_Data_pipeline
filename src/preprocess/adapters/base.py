"""어댑터 공통 dataclass + helper (v2.0 §8 공통 추출 패턴)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from src.utils.excel import SheetData

_WS_RE = re.compile(r"\s+")


@dataclass
class SheetMeta:
    """시트 상단에서 추출한 메타 (base/new model, buyer 등)."""

    base_model_code: str | None = None
    new_model_code: str | None = None
    buyer_base: str | None = None
    buyer_new: str | None = None
    sheet_grade: str | None = None
    raw_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedRow:
    """어댑터가 반환하는 한 행. (core, payload, semantic, source_meta)."""

    core: dict[str, Any]
    payload: dict[str, Any]
    semantic: dict[str, str]
    source_meta: dict[str, Any]


@dataclass
class BomExtraction:
    """BOM 어댑터 전용 출력."""

    parts: list[dict[str, Any]] = field(default_factory=list)
    bom_edges: list[dict[str, Any]] = field(default_factory=list)


# --- Helpers -------------------------------------------------------------


def normalize_cell_text(value: object) -> str:
    """셀 값 → strip + NFC + 다중공백 정리된 문자열."""
    if value is None:
        return ""
    nfc = unicodedata.normalize("NFC", str(value))
    return _WS_RE.sub(" ", nfc.strip())


def cell_at(rows: list[list[Any]], row_1: int, col_1: int) -> Any:
    """1-based 인덱스 셀 (없으면 None)."""
    r = row_1 - 1
    c = col_1 - 1
    if r < 0 or r >= len(rows):
        return None
    row = rows[r]
    if c < 0 or c >= len(row):
        return None
    return row[c]


def iter_data_rows(sheet: SheetData, start_row_1based: int):
    """``start_row_1based``부터 끝까지 (row_index_1based, row) iterator."""
    for idx, row in enumerate(sheet.rows[start_row_1based - 1 :], start=start_row_1based):
        yield idx, row


def is_blank_row(row: list[Any]) -> bool:
    return all(c is None or (isinstance(c, str) and not c.strip()) for c in row)


def parse_multi_header(
    sheet: SheetData,
    header_rows_1based: list[int],
    separator: str = " > ",
    fill_cap: int = 8,
    min_values_to_fill: int = 2,
) -> dict[int, str]:
    """멀티 헤더 행들을 결합해 ``{col_idx_1based: "대분류 > 중분류 > 컬럼명"}`` 반환.

    forward-fill 규칙 (병합 셀이 leftmost 셀에만 값을 두는 패턴 대응):
      - 한 행에 non-empty 셀이 ``min_values_to_fill`` 미만이면 fill 안 함
        (파일 전체 마커 행, buyer 행 등이 과도하게 spread 되는 것 방지)
      - fill 거리는 ``fill_cap`` 컬럼까지 (실데이터 섹션 폭 한계)
    """
    if not header_rows_1based:
        return {}
    n_cols = sheet.max_col
    per_row: list[list[str]] = []
    for r in header_rows_1based:
        row_vals = [normalize_cell_text(cell_at(sheet.rows, r, c)) for c in range(1, n_cols + 1)]
        present_count = sum(1 for v in row_vals if v)
        if present_count < min_values_to_fill:
            # forward-fill 안 함 — 그대로 사용
            per_row.append(row_vals)
            continue
        # bounded forward-fill
        last = ""
        gap = 0
        filled: list[str] = []
        for v in row_vals:
            if v:
                last = v
                gap = 0
                filled.append(v)
            else:
                gap += 1
                if last and gap <= fill_cap:
                    filled.append(last)
                else:
                    filled.append("")
        per_row.append(filled)

    out: dict[int, str] = {}
    for c in range(1, n_cols + 1):
        parts = [row[c - 1] for row in per_row if row[c - 1]]
        # 중복 인접 제거 ("공통" > "공통" > "P/No.")
        deduped: list[str] = []
        for p in parts:
            if not deduped or deduped[-1] != p:
                deduped.append(p)
        if deduped:
            out[c] = separator.join(deduped)
    return out
