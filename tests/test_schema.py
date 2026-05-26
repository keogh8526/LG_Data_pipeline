"""v2.0 Core 13 Pydantic 스키마 + axiom 회귀."""

from __future__ import annotations

import pytest

from src.ontology import axioms
from src.ontology.schema import (
    BOMEdge,
    ChangeEvent,
    ChangeType,
    CoreFields,
    Grade,
    PartType,
    Region,
    export_schema_json,
)


def test_core_minimal_required():
    core = CoreFields(
        part_no="AGG74419321",
        part_name="Packing",
        new_model_code="WSED7667M.ABMQEUR",
        grade="Best-1",
        change_type="Change",
    )
    assert core.part_no == "AGG74419321"
    assert core.change_type == ChangeType.CHANGE.value


def test_core_change_type_alias():
    core = CoreFields(
        part_no="AGG74419321",
        part_name="Packing",
        new_model_code="W7M",
        grade="Best-1",
        change_type="신규",
    )
    assert core.change_type == ChangeType.NEW.value


def test_part_no_normalization():
    assert axioms.normalize_part_no(" agg-744 19321 ") == "AGG74419321"


def test_grade_alias_mapping():
    assert axioms.normalize_grade("Best1") == "Best-1"


def test_axiom_widened_model_code():
    # 실측 WS7D7610B (영문/숫자 혼재, suffix 없음) — 본 패턴이 수용해야 함.
    assert axioms.validate_model_code("WS7D7610B")
    assert axioms.validate_model_code("WSED7667M.ABMQEUR")


def test_parse_grade_from_sheet_name():
    assert axioms.parse_grade_from_sheet_name("변경부품 list_Best1") == "Best-1"
    assert axioms.parse_grade_from_sheet_name("변경부품 list (BK STS)") == "Good-1 BK"
    assert axioms.parse_grade_from_sheet_name("Master(Best)") == "Best-1"


# ── B-6 회귀: family + BK/STS 정밀 분리 ──


def test_parse_grade_family_with_sts_suffix():
    """family + STS 명시 시 family 그대로 유지 (이전엔 무조건 Good-1 BK)."""
    # "Best STS" → "Best-1 STS" (rank 1 기본). normalize_grade alias가 있으면 그것 사용.
    result = axioms.parse_grade_from_sheet_name("Master Best STS")
    assert result is not None
    # family가 Best여야 함 (Good 아님)
    assert result.startswith("Best"), f"expected Best family, got {result}"


def test_parse_grade_family_with_bk_rank():
    """family + rank + BK 모두 명시 — Good-2 BK 같은 경우."""
    result = axioms.parse_grade_from_sheet_name("변경부품 list_Good-2 BK")
    assert result == "Good-2 BK"


def test_parse_grade_lone_bk_sts_fallback():
    """family 없이 BK STS만 있을 때만 historical fallback (Good-1 BK)."""
    assert axioms.parse_grade_from_sheet_name("(BK STS)") == "Good-1 BK"


def test_parse_grade_normal_family_doesnt_trigger_bk_sts():
    """그냥 'Best-1' 시트명이 BK 또는 STS 토큰 없으면 BK 매핑 안 됨."""
    assert axioms.parse_grade_from_sheet_name("Master_Best-1") == "Best-1"
    assert axioms.parse_grade_from_sheet_name("Better-2") == "Better-2"


def test_change_event_minimal():
    core = CoreFields(
        part_no="AGG74419321",
        part_name="Packing",
        new_model_code="WSED7667M.ABMQEUR",
        grade="Best-1",
        change_type="Change",
    )
    ev = ChangeEvent(
        core=core,
        payload={"공통 > 부품 P/No": "AGG74419321"},
        semantic_text={},
        form_version="변경부품_list_96",
        source_file="x.xlsx",
        source_sheet="변경부품 list",
        source_row=5,
        run_id="run_test",
    )
    assert ev.run_id == "run_test"
    assert ev.payload["공통 > 부품 P/No"] == "AGG74419321"


def test_export_schema_json(tmp_path):
    out = tmp_path / "schema.json"
    export_schema_json(out)
    text = out.read_text(encoding="utf-8")
    assert "CoreFields" in text and "ChangeEvent" in text


def test_unknown_change_type_raises():
    with pytest.raises(ValueError):
        CoreFields(
            part_no="AGG74419321",
            part_name="x",
            new_model_code="W7M",
            grade="Best-1",
            change_type="알수없는유형",
        )
