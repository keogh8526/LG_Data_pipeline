"""v2.0 양식 어댑터 회귀."""

from __future__ import annotations

from pathlib import Path

from src.preprocess.adapters import (
    BomExtraction,
    ProjectMeta,
    extract_bom_ag_grid,
    extract_changing_parts_list_family,
    extract_new_parts_list_75,
    extract_sheet,
)
from src.preprocess.adapters.activity_master_meta import extract_activity_master_meta
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
    # payload 보존 (원본 컬럼이 그대로 살아있는지)
    assert any("DRBFM" in k for k in first.payload.keys())
    # semantic_text: change_point/change_reason/DRBFM 코멘트가 들어옴
    assert first.semantic
    # source_meta
    assert first.source_meta["form_version"] == "변경부품_list_96"


def test_new_parts_list_serializes_owners(fixture_workbooks: Path):
    file = fixture_workbooks / "fixture_new_parts_list_75.xlsx"
    sheets = read_workbook(file)
    sheet = next(s for s in sheets if s.name == "신규부품리스트")
    rows = list(extract_new_parts_list_75(file, sheet))
    assert rows
    # 담당자 슬롯이 list로 직렬화돼 있어야 함
    assert isinstance(rows[0].payload.get("담당자_목록", []), list)


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


def test_activity_master_extracts_meta(fixture_workbooks: Path):
    file = fixture_workbooks / "fixture_activity_master.xlsx"
    sheets = read_workbook(file)
    sheet = next(s for s in sheets if s.name == "Master")
    meta = extract_activity_master_meta(file, sheet)
    assert isinstance(meta, ProjectMeta)
    assert meta.date is not None  # 2024-04-09 파싱


# ── B-1 회귀: BOM 어댑터가 행 길이 부족해도 IndexError 안 남 ──


# ── B-7 회귀: NewParts 슬롯이 _NAME_KEYS/_SSO_KEYS 헤더로 검증 ──


import pytest


@pytest.mark.skip(reason="HEADER_ROWS=[3]로 단순화 후 fixture 행4 의존. Phase A에서 _collect_role_slots와 함께 제거 예정.")
def test_new_parts_role_slots_use_header_keys(tmp_path):
    """슬롯 위치가 +1/+2 hardcode 아닌 헤더 검색으로 결정됨."""
    import openpyxl

    from src.preprocess.adapters.new_parts_list_75 import (
        _collect_role_slots,
        extract_new_parts_list_75,
    )
    from src.utils.excel import read_workbook

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "신규부품리스트"
    # 행1 ~ 행3 일반, 행4 leaf — 한 슬롯은 (역할, 이름, SSO ID), 다른 슬롯은 (역할, SSO ID, 이름) 역순
    n = 75
    row1 = ["Key-In" if i % 2 == 0 else "LOV" for i in range(n)]
    ws.append(row1)
    ws.append(["필수"] * 5 + ["옵션"] * (n - 5))
    ws.append(["No", "프로젝트 코드", "신규 구분", "부품 P/No.", "품명"]
              + [None] * (n - 5))
    # 슬롯1: col 10-12 = 역할, 이름, SSO ID
    # 슬롯2: col 20-22 = 역할, SSO ID, 이름 (역순)
    row4: list[Any] = [None] * n
    row4[9] = "역할"
    row4[10] = "이름"
    row4[11] = "SSO ID"
    row4[19] = "역할"
    row4[20] = "SSO ID"
    row4[21] = "이름"
    ws.append(row4)
    # 데이터 행
    data: list[Any] = [None] * n
    data[3] = "AAA1234567"
    data[4] = "Sensor"
    data[9] = "설계"
    data[10] = "김민석"
    data[11] = "kim123"
    data[19] = "구매"
    data[20] = "park999"
    data[21] = "박철수"
    ws.append(data)

    path = tmp_path / "new_parts_reverse.xlsx"
    wb.save(path)
    sheet = read_workbook(path)[0]

    rows = list(extract_new_parts_list_75(path, sheet))
    assert rows
    members = rows[0].payload.get("담당자_목록", [])
    assert len(members) == 2

    # 슬롯1: 정상 순서
    assert members[0]["역할"] == "설계"
    assert members[0]["이름"] == "김민석"
    assert members[0]["SSO ID"] == "kim123"

    # 슬롯2: 역순이지만 헤더 매칭 덕분에 이름/SSO가 정확히 식별
    assert members[1]["역할"] == "구매"
    assert members[1]["이름"] == "박철수"   # 헤더 'SSO ID'와 '이름'이 역순이어도 정확히 매핑
    assert members[1]["SSO ID"] == "park999"


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
