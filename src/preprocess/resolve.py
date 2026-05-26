"""v2.0 (D-011 후) Step 4 — Entity Resolution 단순화.

이전 3-band ER (auto-merge / needs_review / 별개) + suppliers/part_names fuzzy 매칭은
D-011 Phase C에서 제거. 현재는 정확 일치 기반의 단순 dedup만:

  resolve_parts(part_nos)    → set[str]  (axiom 정규화 후 set)
  resolve_models(model_codes) → set[str]  (정확 일치 dedup)
  parse_model_code(code)     → ModelParts (region 추출용 — 유지)

배경: BOM Agent가 ER 결과(needs_review queue, fuzzy alias)에 의존하지 않음.
공급사/부품명 fuzzy 매칭은 추후 필요 시 별도 모듈로 재도입.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

from src.ontology import axioms
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class ModelParts:
    """모델코드 분해 결과 (region 추출용)."""

    model_name: str
    grade_suffix: str | None
    region: str | None


_MODEL_RE = re.compile(r"^(?P<name>[A-Z][A-Z0-9]{2,18})(?:\.(?P<suffix>[A-Z0-9.]+))?$")


# --- Parts (정확 일치 dedup) -------------------------------------------


def resolve_parts(raw_part_nos: Iterable[str]) -> set[str]:
    """부품번호 정규화 + dedup. canonical_id 집합 반환.

    빈 / None 입력은 제외. axiom 위반도 (validation은 quarantine 단계에서) 그대로 포함.
    """
    out: set[str] = set()
    for raw in raw_part_nos:
        if raw is None or not str(raw).strip():
            continue
        out.add(axioms.normalize_part_no(str(raw)))
    return out


# --- Models (region 추출 + dedup) ---------------------------------------


def parse_model_code(model_code: str) -> ModelParts:
    """모델코드 분해.

    예: ``"WSED7667M.ABMQEUR"`` → name ``WSED7667M``, suffix ``ABM``, region ``EUR``.
    """
    if not model_code:
        return ModelParts(model_name="", grade_suffix=None, region=None)
    cleaned = axioms.normalize_model_code(model_code)
    m = _MODEL_RE.match(cleaned)
    if m is None:
        return ModelParts(model_name=cleaned, grade_suffix=None, region=None)
    suffix = m.group("suffix")
    region = None
    if suffix:
        for known in ("EUR", "EUE", "EAP", "SJ", "SA", "AU", "SG", "KR", "US"):
            if suffix.endswith(known):
                region = known
                break
    grade_suffix = suffix[:3] if suffix and len(suffix) >= 3 else suffix
    return ModelParts(model_name=m.group("name"), grade_suffix=grade_suffix, region=region)


def resolve_models(raw_model_codes: Iterable[str]) -> set[str]:
    """모델코드 정규화 + dedup."""
    out: set[str] = set()
    for raw in raw_model_codes:
        if raw is None or not str(raw).strip():
            continue
        out.add(axioms.normalize_model_code(str(raw)))
    return out
