"""v2.0 §8-2 — 신규부품리스트 (75col, BO24 패밀리) 어댑터.

4행 멀티 헤더:
  행1: Key-In / LOV / N/A (입력 방식)
  행2: 필수 / 옵션 / 조건부
  행3: 실제 컬럼명 (No, 프로젝트 코드, 신규 구분, 부품 P/No., 품명, ...)
  행4: 세부 그룹 (담당자 15명 슬롯의 역할/이름/SSO ID 등)

특이사항: 담당자 15명 슬롯은 payload["담당자_목록"]에 list로 직렬화.
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

# 실측 신규부품리스트: row 1=Key-In/LOV, row 2=필수/옵션, row 3=실 헤더, row 4 빈,
# 데이터 row 5+. row 3만 사용해 leaf 헤더 path 생성.
HEADER_ROWS = [3]
DATA_START_ROW = 5

# 행4의 담당자 그룹 키워드 — 역할/이름/SSO ID 셋트가 반복
_ROLE_KEYS = {"역할", "담당", "Role"}
_NAME_KEYS = {"이름", "성명", "Name"}
_SSO_KEYS = {"SSO ID", "사번", "ID"}


def _matches_keys(text: str, keys: set[str]) -> bool:
    t = text.strip()
    return any(k in t for k in keys)


def _is_role_subheader(text: str) -> bool:
    return _matches_keys(text, _ROLE_KEYS)


def _collect_role_slots(
    sheet: SheetData,
    headers: dict[int, str],
) -> list[tuple[int | None, int | None, int | None]]:
    """담당자 슬롯 (role_col, name_col, sso_col) 튜플 리스트.

    B-7: 단순 +1/+2 가정 대신 _NAME_KEYS/_SSO_KEYS 헤더 검증으로 보강.
    슬롯 그룹은 role을 시작점으로 다음 role 직전까지의 헤더에서 name/sso를 탐색.
    """
    role_cols = sorted(
        c for c, h in headers.items() if _is_role_subheader(h.split(" > ")[-1])
    )
    if not role_cols:
        return []

    n_cols = sheet.max_col
    slots: list[tuple[int | None, int | None, int | None]] = []
    boundaries = role_cols + [n_cols + 1]  # 다음 슬롯 시작 또는 sentinel
    for idx, rc in enumerate(role_cols):
        next_role = boundaries[idx + 1]
        # role 다음 ~ 다음 role 직전까지 — name/sso 헤더 탐색
        nc: int | None = None
        sc: int | None = None
        for c in range(rc + 1, min(next_role, n_cols + 1)):
            leaf = headers.get(c, "").split(" > ")[-1]
            if nc is None and _matches_keys(leaf, _NAME_KEYS):
                nc = c
            elif sc is None and _matches_keys(leaf, _SSO_KEYS):
                sc = c
            if nc is not None and sc is not None:
                break
        # fallback — 헤더에서 못 찾았으면 인접 col 추정 (legacy 동작 유지)
        if nc is None and rc + 1 <= n_cols:
            nc = rc + 1
        if sc is None and rc + 2 <= n_cols:
            sc = rc + 2
        slots.append((rc, nc, sc))
    return slots


def extract_new_parts_list_75(
    file_path: Path,
    sheet: SheetData,
    file_meta: dict[str, Any] | None = None,
    cdict: ColumnDictionary | None = None,
) -> Iterable[ExtractedRow]:
    cdict = cdict or load_column_dictionary()
    file_meta = file_meta or {}
    headers = parse_multi_header(sheet, HEADER_ROWS)
    role_slots = _collect_role_slots(sheet, headers)

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

        # 담당자 슬롯 직렬화. B-7: nc/sc가 None이거나 행 길이 초과면 빈 값으로 처리.
        def _safe(col: int | None) -> str:
            if col is None or col - 1 < 0 or col - 1 >= len(row):
                return ""
            return normalize_cell_text(row[col - 1])

        members: list[dict[str, Any]] = []
        for rc, nc, sc in role_slots:
            role = _safe(rc)
            name = _safe(nc)
            sso = _safe(sc)
            if role or name or sso:
                members.append({"역할": role or None, "이름": name or None, "SSO ID": sso or None})
        if members:
            payload["담당자_목록"] = members

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
