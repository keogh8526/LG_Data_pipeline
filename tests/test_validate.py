"""v2.0 검증 모듈 회귀 — 특히 C-2 payload_preservation 회귀 보호."""

from __future__ import annotations

import json

import pandas as pd

from src.preprocess.validate import (
    _measure_payload_preservation,
    _payload_has_content,
    validate_dataframe,
)


# ── C-2 회귀: payload_preservation이 JSON string도 인정해야 함 ──


def test_payload_present_dict():
    assert _payload_has_content({"key": "value"}) is True


def test_payload_present_json_string():
    """pipeline.py가 json.dumps로 직렬화한 후에도 보존률 측정 정확."""
    serialized = json.dumps({"공통 > 부품 P/No": "AGG74419321"})
    assert _payload_has_content(serialized) is True


def test_payload_empty_dict_false():
    assert _payload_has_content({}) is False


def test_payload_empty_string_false():
    assert _payload_has_content("") is False
    assert _payload_has_content("{}") is False
    assert _payload_has_content("null") is False


def test_payload_none_false():
    assert _payload_has_content(None) is False


def test_payload_invalid_json_false():
    assert _payload_has_content("not valid json") is False


def test_measure_payload_preservation_json_strings():
    """C-2: pipeline parquet 저장 후의 모양과 동일하게 검증."""
    df = pd.DataFrame(
        [
            {"payload": json.dumps({"a": 1})},
            {"payload": json.dumps({"b": 2})},
            {"payload": json.dumps({"c": 3})},
        ]
    )
    rate = _measure_payload_preservation(df)
    assert rate == 1.0, f"expected 1.0 with JSON strings, got {rate}"


def test_measure_payload_preservation_mixed():
    """일부 dict / 일부 string / 일부 빈 — 부분 보존 비율 정확."""
    df = pd.DataFrame(
        [
            {"payload": {"a": 1}},
            {"payload": json.dumps({"b": 2})},
            {"payload": "{}"},          # 빈 dict로 dumps된 경우
            {"payload": None},
        ]
    )
    rate = _measure_payload_preservation(df)
    assert rate == 0.5, f"expected 0.5 (2/4), got {rate}"


# ── validate_dataframe 통합 ──


def test_validate_pipeline_serialized_df_acceptable():
    """C-2 통합: pipeline이 직렬화한 DataFrame을 validate가 통과시켜야 함."""
    df = pd.DataFrame(
        [
            {
                "part_no": "AGG74419321",
                "part_name": "Packing",
                "new_model_code": "WSED7667M.ABMQEUR",
                "grade": "Best-1",
                "change_type": "Change",
                "base_part_no": "AGG74419320",
                "base_model_code": "WSED7667M",
                "region": "EUR",
                "event_stage": "DV",
                "change_point": "내열 220→240",
                "change_reason": "신규 규제",
                "bom_level": 1,
                "part_type": "Assy",
                "payload": json.dumps({"공통 > 부품 P/No": "AGG74419321"}),
            }
        ]
    )
    report = validate_dataframe(df, run_id="test_run", rows_in=1)
    assert report.payload_preservation_rate == 1.0
    # 다른 지표도 통과해야 commit 가능
    failures = report.critical_failures()
    assert "payload_preservation_rate" not in failures, (
        f"payload_preservation should pass but found in failures: {failures}"
    )
