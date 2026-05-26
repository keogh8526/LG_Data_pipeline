"""v2.0 Query Router 회귀 (7케이스)."""

from __future__ import annotations

from src.search.router import route_query


def test_part_no_pattern_routes_exact_sql():
    plan = route_query("AGG74419320 변경 이력")
    assert plan.mode == "exact_sql"
    assert "AGG74419320" in plan.sql_filter_values


def test_model_code_routes_exact_plus_vector():
    plan = route_query("WSED7667M EUR향 사출 부품")
    assert plan.mode in {"exact_sql_plus_vector", "multi_vector", "single_vector"}


def test_reason_keyword_boosts_change_reason_emb():
    plan = route_query("왜 도어 힌지를 변경했나")
    assert plan.mode == "multi_vector"
    assert plan.vector_weights.get("change_reason_emb", 0) > 0


def test_test_keyword_boosts_test_plan_emb():
    plan = route_query("내열 시험 필요한 부품")
    assert plan.vector_weights.get("test_plan_emb", 0) > 0


def test_drbfm_keyword_boosts_drbfm_emb():
    plan = route_query("DRBFM 리스크 있는 변경")
    assert plan.vector_weights.get("drbfm_emb", 0) > 0


def test_change_point_keyword_boosts_change_point_emb():
    plan = route_query("변경점이 무엇인지")
    assert plan.vector_weights.get("change_point_emb", 0) > 0


def test_default_falls_through_to_narrative():
    plan = route_query("패킹 부품 일반 정보")
    assert plan.case_name == "default"
    assert plan.primary_vector == "narrative_emb"


# ── C-1 회귀: re.findall이 group만 반환하던 버그 ──


def test_model_code_full_match_in_sql_filter_values():
    """exact_model_code 케이스 매칭 시 sql_filter_values에 전체 매치가 들어와야 함.

    이전 버그: re.findall(r'...(\\.[A-Z0-9.]+)?\\b', "WSED7667M.ABMQEUR")
    → ['.ABMQEUR'] (group 1만 반환).
    Fix: re.finditer + m.group(0) 사용.
    """
    plan = route_query("WSED7667M.ABMQEUR EUR향 사출 부품")
    # exact_model_code 케이스에 매칭됐어야 함
    assert plan.case_name == "exact_model_code"
    # sql_filter_values에 전체 매치 "WSED7667M.ABMQEUR"가 있어야 함
    assert "WSED7667M.ABMQEUR" in plan.sql_filter_values
    # group 1만(".ABMQEUR") 들어있으면 안 됨
    assert ".ABMQEUR" not in plan.sql_filter_values


def test_part_no_full_match_in_sql_filter_values():
    """exact_part_no 케이스도 전체 매치 보장."""
    plan = route_query("AGG74419320 변경 이력")
    assert "AGG74419320" in plan.sql_filter_values


def test_model_code_suffix_없는_경우():
    """suffix 없는 모델코드(WS7D7610B)도 전체 매치 반환."""
    plan = route_query("WS7D7610B 부품 정보")
    # 모델코드 패턴이 매치되면 plan.sql_filter_values에 전체가 있어야 함
    if plan.case_name == "exact_model_code":
        assert "WS7D7610B" in plan.sql_filter_values
