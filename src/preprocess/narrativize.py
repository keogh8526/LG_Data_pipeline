"""v2.0 (D-011 Phase F 후) — 결정론적 narrativizer.

core dict → 자연어 4~6문장. LLM 호출 0회. payload는 사용 안 함 (이전 6 조건절
trigger 모두 제거됨).

행 1개당 narrative_text 1개 생성. 검색 임베딩(narrative_emb) 소스.
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


def _value_present(v: Any) -> bool:
    return v is not None and str(v).strip() != ""


def _fill_clause(template: str, vars_: dict[str, Any]) -> str:
    """{var} 치환. 모든 var가 채워졌고 비어있지 않으면 결과 반환, 아니면 ''."""
    try:
        keys_in_template = re.findall(r"\{(\w+)\}", template)
        for k in keys_in_template:
            if not _value_present(vars_.get(k)):
                return ""
        return template.format(**vars_)
    except (KeyError, IndexError):
        return ""


def narrativize(
    core: dict[str, Any],
    payload: dict[str, Any] | None = None,  # 호환성 유지 (인자만 받고 미사용)
) -> str:
    """Core → 자연어 narrative_text.

    D-011 Phase F: payload trigger 6개(drbfm/hsms/mold/test/supplier/nonstd) 제거.
    핵심 절(part_meta/model_meta/base_part/change_point/change_reason/stage)만 조립.

    Args:
        core: 정규화된 Core dict.
        payload: 호환성 유지를 위한 인자 (D-011 후 미사용).

    Returns:
        자연어 string. 빈 입력은 짧은 문장만 반환.
    """
    tmpl = _templates()
    clauses = tmpl.get("clauses", {})
    change_type_korean = tmpl.get("change_type_korean", {})
    stage_korean = tmpl.get("event_stage_korean", {})

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

    part_meta = _fill_clause(clauses.get("part_meta_clause", ""), vars_)
    model_meta = _fill_clause(clauses.get("model_meta_clause", ""), vars_)
    base_part = _fill_clause(clauses.get("base_part_clause", ""), vars_)
    change_point_c = _fill_clause(clauses.get("change_point_clause", ""), vars_)
    change_reason_c = _fill_clause(clauses.get("change_reason_clause", ""), vars_)

    ct = str(core.get("change_type", "")).strip()
    change_type_phrase = change_type_korean.get(ct, ct)

    es = str(core.get("event_stage", "")).strip()
    stage_phrase = stage_korean.get(es)
    stage_clause = (
        clauses.get("stage_clause", "").format(event_stage_korean=stage_phrase)
        if stage_phrase
        else ""
    )

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
        sentence_pieces.append(
            change_point_c if change_point_c.endswith(".") else change_point_c + "."
        )
    if change_reason_c:
        sentence_pieces.append(
            change_reason_c if change_reason_c.endswith(".") else change_reason_c + "."
        )
    if stage_clause:
        sentence_pieces.append(stage_clause)

    narrative = " ".join(s for s in sentence_pieces if s).strip()
    narrative = re.sub(r"\s+", " ", narrative)
    return narrative
