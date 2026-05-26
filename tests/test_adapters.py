"""v2.0 양식 어댑터 회귀 (D-011 간소화 후)."""

from __future__ import annotations

from pathlib import Path

from src.preprocess.adapters import (
    BomExtraction,
    extract_bom_ag_grid,
    extract_changing_parts_list_family,
    extract_new_parts_list_75,
    extract_sheet,
)
from src.utils.excel import read_workbook


def test_changing_parts_extracts_core(fixture_workbooks: Path):
    file = fixture_workbooks / "fixture_changing_parts_96.xlsx"
    sheets = read_workbook(file)
    assert sheets
    sheet = sheets[0]
    rows = list(extract_changing_parts_list_family(file, sheet))
    assert len(rows) >= 1
    first = rows[0]
    # core
    assert first.core.get("part_no") == "AGG74419321"
    assert first.core.get("part_name") == "Packing Assembly"
    assert first.core.get("change_point")
    # payload 보존 (원본 컬럼이 그대로 살아있는지). D-011 후에도 ExtractedRow.payload는
    # 어댑터 내부에서 모든 헤더를 받아옴 (pipeline에서 extra_fields로 분리).
    assert any("DRBFM" in k for k in first.payload.keys())
    # D-011 Phase E: semantic dict는 더 이상 채워지지 않음 (멀티 vector 제거).
    # source_meta
    assert first.source_meta["form_version"] == "변경부품_list_96"


def test_new_parts_list_extracts_rows(fixture_workbooks: Path):
    """D-011: 담당자_목록 직렬화는 제거됨 — 일반 row 추출만 검증."""
    file = fixture_workbooks / "fixture_new_parts_list_75.xlsx"
    sheets = read_workbook(file)
    sheet = next(s for s in sheets if s.name == "신규부품리스트")
    rows = list(extract_new_parts_list_75(file, sheet))
    assert rows, "신규부품리스트에서 최소 1행 추출"


def test_bom_ag_grid_emits_parts_and_edges(fixture_workbooks: Path):
    file = fixture_workbooks / "fixture_bom_ag_grid_36.xlsx"
    sheets = read_workbook(file)
    sheet = sheets[0]
    result = extract_bom_ag_grid(file, sheet, {"run_id": "test_run"})
    assert isinstance(result, BomExtraction)
    assert len(result.parts) > 0
    # bom_edges는 level 기반 스택으로 일부 생성돼야 함 (Lvl 1,2,3 반복)
    assert len(result.bom_edges) > 0


def test_dispatcher_routes_correctly(fixture_workbooks: Path):
    file = fixture_workbooks / "fixture_changing_parts_96.xlsx"
    sheets = read_workbook(file)
    out = extract_sheet(file, sheets[0], "변경부품_list_96", {"run_id": "r1"})
    assert isinstance(out, list)
    assert out and out[0].core.get("part_no")


# D-011: test_activity_master_extracts_meta + test_new_parts_role_slots_use_header_keys 제거.
# 어댑터 자체가 사라졌으므로 회귀 테스트도 무의미.


# ── B-1 회귀: BOM 어댑터가 행 길이 부족해도 IndexError 안 남 ──


def test_bom_ag_grid_handles_short_rows(tmp_path):
    """parent col이 있고 일부 행이 그보다 짧으면 _safe_cell이 None 반환."""
    import openpyxl

    from src.preprocess.adapters.bom_ag_grid import extract_bom_ag_grid

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ag-grid"
    # 36 columns 명세, parent col을 일부러 뒤쪽에 두기
    headers = ["P/No", "Desc.", "Lvl", "Qty"] + [f"c{i}" for i in range(4, 35)] + ["부모 P/No"]
    ws.append(headers)
    # 정상 행: 모든 col 채움 (36개)
    ws.append(["AGP0000001", "Part 1", 1, 1.0] + [None] * 31 + ["AGP0000000"])
    # 짧은 행: parent col 위치까지 안 채워짐 (4개만)
    ws.append(["AGP0000002", "Part 2", 2, 1.0])
    # 더 짧은 행 — 100개 이상 필요 (min_row 체크용)
    for i in range(100):
        ws.append([f"AGP{i:07d}", f"P {i}", 1, 1.0])
    path = tmp_path / "short_bom.xlsx"
    wb.save(path)

    from src.utils.excel import read_workbook
    sheets = read_workbook(path)
    # 짧은 행에서 row[col_parent-1] 직접 인덱싱하던 버그면 IndexError 발생
    result = extract_bom_ag_grid(path, sheets[0], {"run_id": "test"})
    # 정상 처리 (IndexError 안 남) — parts는 추출됨
    assert len(result.parts) > 0
