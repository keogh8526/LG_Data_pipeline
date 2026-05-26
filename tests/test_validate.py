"""v2.0 (D-011 후) 검증 모듈 회귀.

이전 payload_preservation 검증은 D-011 Phase B에서 제거됨 (validate.py 7 지표 축소).
이 파일은 validate_dataframe의 핵심 동작만 확인.
"""

from __future__ import annotations

import json

import pandas as pd

from src.preprocess.validate import THRESHOLDS, validate_dataframe


def test_validate_minimal_acceptable():
    """Core 13 모두 채워진 깨끗한 1행이 모든 지표 통과해야 함."""
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
                "extra_fields": json.dumps({"공통 > 부품 P/No": "AGG74419321"}),
            }
        ]
    )
    report = validate_dataframe(df, run_id="test_run", rows_in=1)
    failures = report.critical_failures()
    assert not failures, f"expected no failures, got {failures}"


def test_validate_quarantine_in_axiom_rate():
    """quarantine된 행은 axiom_violation_rate에 반영."""
    df = pd.DataFrame(
        [
            {"part_no": "OK1234567", "_quarantine_reason": None},
            {"part_no": "BAD",       "_quarantine_reason": "part_no=axiom failed"},
        ]
    )
    report = validate_dataframe(df, run_id="x", rows_in=2)
    assert report.axiom_violation_rate == 0.5


def test_validate_thresholds_keys():
    """D-011 후 THRESHOLDS dict 키 6개만 (payload_preservation_rate 제거)."""
    expected = {
        "column_match",
        "type_match",
        "value_format_match",
        "row_preservation",
        "null_rate_required_max",
        "axiom_violation_rate_max",
    }
    assert set(THRESHOLDS.keys()) == expected
