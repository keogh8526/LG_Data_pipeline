"""v2.0 Step 5 ★ — 결정론적 narrativizer (preprocessing_v2.md §5, §6).

core + payload → 자연어 4~8문장 (200~600 토큰). LLM 호출 0회.
조건부 절은 값이 있을 때만 채워지고, 비어 있으면 빈 칸 없이 자연스럽게 생략.

행 1개당 narrative_text 1개 생성. 검색 메인 임베딩(narrative_emb)의 소스.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import yaml

from src.utils.logging import get_logger
from src.utils.paths import NARRATIVIZE_TEMPLATES_PATH

log = get_logger(__name__)


@lru_cache(maxsize=1)
def _templates() -> dict[str, Any]:
    return yaml.safe_load(NARRATIVIZE_TEMPLATES_PATH.read_text(encoding="utf-8"))


def _get_payload_value(payload: dict[str, Any], keys: list[str]) -> Any:
    """payload에서 keys 중 첫 매칭 값 반환."""
    for k in keys:
        for actual_key, value in payload.items():
            if actual_key == k or actual_key.endswith(f" > {k.split(' > ')[-1]}"):
                if value not in (None, ""):
                    return value
    return None


def _value_present(v: Any) -> bool:
    return v is not None and str(v).strip() != ""


def _fill_clause(template: str, vars_: dict[str, Any]) -> str:
    """{var} 치환. 모든 var가 채워졌고 비어있지 않으면 결과 반환, 아니면 ''."""
    try:
        # 사용된 키들이 모두 vars_에 있고 비어있지 않은지 검사
        keys_in_template = re.findall(r"\{(\w+)\}", template)
        for k in keys_in_template:
            if not _value_present(vars_.get(k)):
                return ""
        return template.format(**vars_)
    except (KeyError, IndexError):
        return ""


def narrativize(
    core: dict[str, Any],
    payload: dict[str, Any] | None = None,
) -> str:
    """Core + payload → 자연어 narrative_text.

    Args:
        core: 정규화된 Core dict (Pydantic 검증 통과 후).
        payload: 양식 원본 dict (header_path → value).

    Returns:
        200~600 토큰 자연어. 빈 입력은 짧은 문장만 반환.
    """
    tmpl = _templates()
    payload = payload or {}
    triggers = tmpl.get("payload_triggers", {})
    clauses = tmpl.get("clauses", {})
    change_type_korean = tmpl.get("change_type_korean", {})
    stage_korean = tmpl.get("event_stage_korean", {})

    # ── 기본 변수 ──
    vars_: dict[str, Any] = {
        "part_no": core.get("part_no", ""),
        "part_name": core.get("part_name", ""),
        "bom_level": core.get("bom_level"),
        "part_type": core.get("part_type"),
        "new_model_code": core.get("new_model_code", ""),
        "region": core.get("region"),
        "grade": core.get("grade"),
        "base_part_no": core.get("base_part_no"),
        "change_point": core.get("change_point"),
        "change_reason": core.get("change_reason"),
    }

    # ── 조건부 부속 절들 채우기 ──
    part_meta = _fill_clause(clauses.get("part_meta_clause", ""), vars_)
    model_meta = _fill_clause(clauses.get("model_meta_clause", ""), vars_)
    base_part = _fill_clause(clauses.get("base_part_clause", ""), vars_)
    change_point_c = _fill_clause(clauses.get("change_point_clause", ""), vars_)
    change_reason_c = _fill_clause(clauses.get("change_reason_clause", ""), vars_)

    # change_type 한국어 문구
    ct = str(core.get("change_type", "")).strip()
    change_type_phrase = change_type_korean.get(ct, ct)

    # event_stage 절
    es = str(core.get("event_stage", "")).strip()
    stage_phrase = stage_korean.get(es)
    stage_clause = (
        clauses.get("stage_clause", "").format(event_stage_korean=stage_phrase)
        if stage_phrase
        else ""
    )

    # ── payload 트리거로 옵션 절 활성화 ──
    optional_parts: list[str] = []

    # DRBFM
    drbfm_trig = triggers.get("drbfm_clause", {})
    drbfm_val = _get_payload_value(payload, drbfm_trig.get("keys", []))
    if _value_present(drbfm_val):
        optional_parts.append(
            clauses.get("drbfm_clause", "").format(drbfm_note=str(drbfm_val).strip())
        )

    # HSMS
    hsms_trig = triggers.get("hsms_clause", {})
    hsms_val = _get_payload_value(payload, hsms_trig.get("keys", []))
    if _value_present(hsms_val):
        contains = hsms_trig.get("value_contains", [])
        if not contains or any(c in str(hsms_val) for c in contains):
            optional_parts.append(clauses.get("hsms_clause", ""))

    # 금형
    mold_trig = triggers.get("mold_clause", {})
    mold_val = _get_payload_value(payload, mold_trig.get("keys", []))
    if _value_present(mold_val):
        optional_parts.append(
            clauses.get("mold_clause", "").format(mold_type=str(mold_val).strip())
        )

    # 시험
    test_trig = triggers.get("test_clause", {})
    test_val = _get_payload_value(payload, test_trig.get("keys", []))
    if _value_present(test_val):
        optional_parts.append(
            clauses.get("test_clause", "").format(test_items=str(test_val).strip())
        )

    # 공급사
    sup_trig = triggers.get("supplier_clause", {})
    sup_val = _get_payload_value(payload, sup_trig.get("keys", []))
    if _value_present(sup_val):
        optional_parts.append(
            clauses.get("supplier_clause", "").format(supplier=str(sup_val).strip())
        )

    # 비표준
    nonstd_trig = triggers.get("nonstd_clause", {})
    nonstd_val = _get_payload_value(payload, nonstd_trig.get("keys", []))
    if _value_present(nonstd_val):
        contains = nonstd_trig.get("value_contains", [])
        if not contains or any(c in str(nonstd_val) for c in contains):
            reason_val = _get_payload_value(payload, nonstd_trig.get("var_keys", []))
            optional_parts.append(
                clauses.get("nonstd_clause", "").format(
                    nonstd_reason=str(reason_val).strip() if reason_val else "사유 미기재"
                )
            )

    optional_block = "".join(optional_parts).strip()

    # ── 최종 조립 ──
    main_template = tmpl.get("change_event_template", "")
    sentence_pieces: list[str] = []

    pn = vars_.get("part_no")
    name = vars_.get("part_name")
    if pn:
        head = f"변경부품 {pn}({name}{part_meta})." if name else f"변경부품 {pn}{part_meta}."
        sentence_pieces.append(head)
    if vars_.get("new_model_code"):
        sentence_pieces.append(f"모델 {vars_['new_model_code']}{model_meta}.")
    if change_type_phrase:
        if base_part:
            sentence_pieces.append(f"{change_type_phrase}{base_part}")
        else:
            sentence_pieces.append(change_type_phrase)
    if change_point_c:
        sentence_pieces.append(change_point_c if change_point_c.endswith(".") else change_point_c + ".")
    if change_reason_c:
        sentence_pieces.append(change_reason_c if change_reason_c.endswith(".") else change_reason_c + ".")
    if optional_block:
        sentence_pieces.append(optional_block.strip())
    if stage_clause:
        sentence_pieces.append(stage_clause)

    narrative = " ".join(s for s in sentence_pieces if s).strip()
    # 다중 공백 정리
    narrative = re.sub(r"\s+", " ", narrative)
    return narrative
