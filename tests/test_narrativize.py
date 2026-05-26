"""v2.0 (D-011 Phase F 후) narrativizer 회귀.

이전 6 조건절(drbfm/hsms/mold/test/supplier/nonstd) + payload_triggers 검증은
제거. 핵심 절(part_meta/model_meta/base_part/change_point/change_reason/stage)만 검증.
"""

from __future__ import annotations

from src.preprocess.narrativize import narrativize


def _full_core() -> dict:
    return {
        "part_no": "AGG74419321",
        "part_name": "Packing Assembly",
        "base_part_no": "AGG74419320",
        "new_model_code": "WSED7667M.ABMQEUR",
        "grade": "Best-1",
        "region": "EUR",
        "change_type": "Change",
        "event_stage": "DV",
        "change_point": "도어 힌지 내열 220→240",
        "change_reason": "신규 안전 규제 대응",
        "bom_level": 1,
        "part_type": "Assy",
    }


def test_narrativize_full_case():
    """핵심 절 모두 포함된 narrative."""
    text = narrativize(_full_core(), payload={})
    for token in (
        "AGG74419321",
        "Packing",
        "WSED7667M",
        "EUR",
        "Best-1",
        "Change",
        "AGG74419320",
        "DV",
    ):
        assert token in text, f"missing token: {token}"


def test_narrativize_no_drbfm_or_hsms():
    """D-011: payload trigger 절(DRBFM/HSMS/금형/시험/공급사/비표준) 모두 없음."""
    text = narrativize(_full_core(), payload={"DRBFM > 코멘트": "고온"})
    # payload는 더 이상 narrative에 영향 X
    assert "DRBFM" not in text
    assert "HSMS" not in text
    assert "공급사" not in text
    assert "금형" not in text


def test_narrativize_missing_optional_core_fields():
    """region/event_stage/base_part_no 없을 때 자연스러운 생략."""
    minimal = {
        "part_no": "AGG74419321",
        "part_name": "Packing",
        "new_model_code": "WSED7667M",
        "grade": "Best-1",
        "change_type": "New",
    }
    text = narrativize(minimal, {})
    assert "AGG74419321" in text
    assert "신규 등록" in text or "New" in text


def test_narrativize_change_type_carry_over():
    core = _full_core()
    core["change_type"] = "Carry-over"
    text = narrativize(core, {})
    assert "유지" in text or "Carry-over" in text
