"""D-012 — 어댑터 공통 dataclass + helper.

ExtractedRow shape change:
  before (v2.0): (core, payload, semantic, source_meta)
  after  (D-012): (dev_part_master_fields, extra_fields, source_meta)

어댑터는 여전히 column_dictionary 기반으로 (core, payload) 중간 dict를
만들지만, 행 마지막에 ``build_extracted_row(core, payload, source_meta, cdict)``
를 호출해 팀원 dev_part_master 컬럼명으로 변환된 단일 ExtractedRow를 반환한다.

BOM 어댑터(bom_ag_grid)도 동일한 ExtractedRow 스트림으로 통일 — bom_edges
테이블이 사라졌으므로 부품 간 hierarchy 정보는 extra_fields의 parent_part_no
키에 보존.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from src.db._mapping import coerce_bom_depth, map_core_to_dpm
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
    """어댑터가 반환하는 한 행 (D-012).

    dev_part_master_fields: 팀원 컬럼명으로 매핑된 dict (part_no_new, event 등).
    extra_fields: Core 13에 매핑된 헤더 제외 + Core 잔여 (grade, event_stage).
    source_meta: source_file, source_sheet, source_row, form_id.
    """

    dev_part_master_fields: dict[str, Any]
    extra_fields: dict[str, Any]
    source_meta: dict[str, Any]


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

    forward-fill 규칙은 base 모듈 v2.0과 동일.
    """
    if not header_rows_1based:
        return {}
    n_cols = sheet.max_col
    per_row: list[list[str]] = []
    for r in header_rows_1based:
        row_vals = [normalize_cell_text(cell_at(sheet.rows, r, c)) for c in range(1, n_cols + 1)]
        present_count = sum(1 for v in row_vals if v)
        if present_count < min_values_to_fill:
            per_row.append(row_vals)
            continue
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
        deduped: list[str] = []
        for p in parts:
            if not deduped or deduped[-1] != p:
                deduped.append(p)
        if deduped:
            out[c] = separator.join(deduped)
    return out


# --- ExtractedRow assembly ------------------------------------------------


def build_extracted_row(
    core: dict[str, Any],
    payload: dict[str, Any],
    source_meta: dict[str, Any],
    cdict: Any,
    extra_native: dict[str, Any] | None = None,
) -> ExtractedRow:
    """Adapter helper: column_dict 기반 (core, payload) → dev_part_master 형식.

    Args:
        core: column_dictionary.lookup으로 매핑된 필드 dict (part_no, change_type 등).
        payload: 원본 헤더 path → 셀 값 (Core 매핑 여부 무관).
        source_meta: source_file/sheet/row/form_id 등.
        cdict: ColumnDictionary (None 헤더가 mapped됐는지 판정용).
        extra_native: 어댑터가 dev_part_master 컬럼명으로 직접 채운 값
            (예: bom_ag_grid의 parent_part_no, supplier 등).

    Returns:
        :class:`ExtractedRow`.
    """
    extra_native = dict(extra_native or {})

    # 1) Core → dpm 컬럼명 변환 + 잔여(grade, event_stage)
    dpm, residual = map_core_to_dpm(core, extra_native)

    # 2) bom_level (Core) → bom_depth + bom_level_raw
    if core.get("bom_level") is not None:
        dpm.setdefault("bom_depth", coerce_bom_depth(core["bom_level"]))
        if "bom_level_raw" not in dpm:
            dpm["bom_level_raw"] = str(core["bom_level"])

    # 3) extra_fields: Core 13에 매핑 안 된 원본 헤더만 + Core 잔여 (grade/event_stage)
    extra_fields: dict[str, Any] = {}
    for header, value in (payload or {}).items():
        if cdict.lookup(header) is None and value is not None:
            extra_fields[header] = value
    extra_fields.update(residual)

    return ExtractedRow(
        dev_part_master_fields=dpm,
        extra_fields=extra_fields,
        source_meta=source_meta,
    )
