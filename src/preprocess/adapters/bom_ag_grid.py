"""v2.0 §8-6 — BOM ag-grid (36col) 어댑터.

특이사항 (다른 어댑터와 다른 점):
  - 출력은 ChangeEvent가 아니라 (parts, bom_edges) tuple
  - 행1 단일 헤더 (간단)
  - parts 테이블 + bom_edges 테이블 양쪽 적재
  - 단일 시트(ag-grid)만 처리
  - 3개 파일: WDEK9429S, LSIU6339XE, base_bom
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.preprocess.adapters.base import (
    BomExtraction,
    cell_at,
    is_blank_row,
    iter_data_rows,
    normalize_cell_text,
)
from src.preprocess.column_dict import ColumnDictionary, load_column_dictionary
from src.utils.excel import SheetData
from src.utils.logging import get_logger

log = get_logger(__name__)

HEADER_ROW = 1
DATA_START_ROW = 2

# 헤더 후보 (column dictionary와 별개 — BOM 전용)
PART_NO_KEYS = ("P/No", "P/No.", "Part No", "PartNo", "PartNumber")
DESC_KEYS = ("Desc.", "Desc", "Description", "품명", "부품명", "Part Name(자)", "Part Name")
# 실측: 'Parent Part No(모)' (base_bom/WDEK/LSIU 공통).
PARENT_KEYS = (
    "부모 P/No", "Parent P/No", "ParentPartNo", "상위 P/No",
    "Parent Part No(모)", "Parent Part No",
)
LEVEL_KEYS = ("Lvl", "Level", "BOM Level", "Lv")
QTY_KEYS = ("Qty", "수량", "Quantity")
MODEL_KEYS = ("모델", "Model", "Model Code", "ModelCode")
CHANGE_IN_KEYS = ("Change In", "ChangeIn", "투입일")
CHANGE_OUT_KEYS = ("Change Out", "ChangeOut", "탈락일")
PART_TYPE_KEYS = ("MechanicalPart", "Part Type", "부품 유형", "Type")


def _find_col(headers: dict[int, str], keys: tuple[str, ...]) -> int | None:
    """헤더에서 keys 중 하나와 일치하는 col 반환."""
    norm = {c: h.strip() for c, h in headers.items()}
    for c, h in norm.items():
        if h in keys:
            return c
    # case-insensitive fallback
    lower = {c: h.lower() for c, h in norm.items()}
    targets = [k.lower() for k in keys]
    for c, h in lower.items():
        if h in targets:
            return c
    return None


def _read_headers(sheet: SheetData) -> dict[int, str]:
    return {
        c: normalize_cell_text(cell_at(sheet.rows, HEADER_ROW, c))
        for c in range(1, sheet.max_col + 1)
        if normalize_cell_text(cell_at(sheet.rows, HEADER_ROW, c))
    }


def _model_from_filename(file_name: str) -> str | None:
    """WDEK9429S.ATTLSNA@CVZ.EKHQ 패턴에서 모델코드 추출."""
    m = re.match(r"^([A-Z]{3,5}\d{3,5}[A-Z]?(?:\.[A-Z0-9.]+)?)", file_name)
    return m.group(1) if m else None


def extract_bom_ag_grid(
    file_path: Path,
    sheet: SheetData,
    file_meta: dict[str, Any] | None = None,
    cdict: ColumnDictionary | None = None,
) -> BomExtraction:
    """BOM ag-grid 시트 → (parts, bom_edges).

    Returns:
        :class:`BomExtraction` (parts/bom_edges 모두 dict 리스트).
    """
    cdict = cdict or load_column_dictionary()
    file_meta = file_meta or {}
    run_id = file_meta.get("run_id", "")
    result = BomExtraction()

    headers = _read_headers(sheet)
    if not headers:
        log.warning("adapter.bom.no_headers", file=file_path.name)
        return result

    col_part = _find_col(headers, PART_NO_KEYS)
    col_desc = _find_col(headers, DESC_KEYS)
    col_parent = _find_col(headers, PARENT_KEYS)
    col_level = _find_col(headers, LEVEL_KEYS)
    col_qty = _find_col(headers, QTY_KEYS)
    col_model = _find_col(headers, MODEL_KEYS)
    col_change_in = _find_col(headers, CHANGE_IN_KEYS)
    col_change_out = _find_col(headers, CHANGE_OUT_KEYS)
    col_ptype = _find_col(headers, PART_TYPE_KEYS)

    file_model = _model_from_filename(file_path.stem)
    parent_stack: list[tuple[int, str]] = []  # (level, part_no)

    def _safe_cell(row: list, col: int | None):
        """1-based col → 셀 값 (행 길이 부족 시 None)."""
        if col is None or col - 1 < 0 or col - 1 >= len(row):
            return None
        return row[col - 1]

    parts_seen: set[str] = set()
    rows_processed = 0
    for row_idx, row in iter_data_rows(sheet, DATA_START_ROW):
        if is_blank_row(row):
            continue
        part_no = normalize_cell_text(_safe_cell(row, col_part))
        if not part_no:
            continue

        desc = normalize_cell_text(_safe_cell(row, col_desc))
        level_raw = _safe_cell(row, col_level)
        # 실측 base_bom/WDEK/LSIU의 Lvl은 '.1', '..2', '...3' 형식 — 점 개수 = depth.
        # 숫자 또는 점 두 가지 형식 모두 처리.
        level: int | None = None
        if level_raw not in (None, ""):
            try:
                level = int(float(level_raw))
            except (TypeError, ValueError):
                s = str(level_raw).strip()
                if s and all(ch == "." for ch in s.replace(s.lstrip("."), "", 1) if not ch.isdigit()):
                    # '.1' / '...3' 형식
                    prefix = s.lstrip(".")
                    try:
                        level = int(prefix) if prefix else s.count(".")
                    except ValueError:
                        level = s.count(".")
                elif s.startswith("."):
                    # 마커 행 (*S*, *Q* 등 제외 후 fallback)
                    level = s.count(".") or None
        qty_raw = _safe_cell(row, col_qty)
        try:
            qty = float(qty_raw) if qty_raw not in (None, "") else None
        except (TypeError, ValueError):
            qty = None
        model = normalize_cell_text(_safe_cell(row, col_model)) if col_model else (file_model or "UNKNOWN")
        if not model:
            model = file_model or "UNKNOWN"
        change_in = normalize_cell_text(_safe_cell(row, col_change_in)) if col_change_in else None
        change_out = normalize_cell_text(_safe_cell(row, col_change_out)) if col_change_out else None
        ptype = normalize_cell_text(_safe_cell(row, col_ptype)) if col_ptype else None

        # parts upsert
        if part_no not in parts_seen:
            parts_seen.add(part_no)
            result.parts.append(
                {
                    "part_no": part_no,
                    "part_name": desc or part_no,
                    "bom_level": level,
                    "part_type": ptype or None,
                    "run_id": run_id,
                    "source_file": file_path.name,
                }
            )

        # parent 결정: explicit col이 있으면 사용, 없으면 level 기반 스택.
        # B-1: 행 길이 부족 시 IndexError 방지 위해 _safe_cell 사용.
        parent: str | None = None
        if col_parent:
            parent = normalize_cell_text(_safe_cell(row, col_parent)) or None
        elif level is not None:
            while parent_stack and parent_stack[-1][0] >= level:
                parent_stack.pop()
            if parent_stack:
                parent = parent_stack[-1][1]
            parent_stack.append((level, part_no))

        if parent and parent != part_no:
            result.bom_edges.append(
                {
                    "model_code": model or "",
                    "parent_part_no": parent,
                    "child_part_no": part_no,
                    "qty": qty,
                    "bom_level": level,
                    "change_in": change_in,
                    "change_out": change_out,
                    "run_id": run_id,
                }
            )
        rows_processed += 1

    log.info(
        "adapter.bom.extracted",
        file=file_path.name,
        parts=len(result.parts),
        edges=len(result.bom_edges),
        rows=rows_processed,
    )
    return result
