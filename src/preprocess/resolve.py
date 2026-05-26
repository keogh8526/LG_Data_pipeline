"""v2.0 Step 4 — Entity Resolution (3-band).

preprocessing_v2.md §3 Step 4. 모든 ER에 3-band 적용:
  score ≥ 0.95 → auto-merge
  0.80 ≤ score < 0.95 → needs_review
  score < 0.80 → 별개 엔티티

대상: part_no, part_name, supplier, model_code.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

from rapidfuzz import fuzz, process

from src.ontology import axioms
from src.utils.logging import get_logger

log = get_logger(__name__)

AUTO_MERGE = 0.95
REVIEW_LOW = 0.80


@dataclass
class ResolvedEntity:
    """ER 결과 한 엔티티."""

    canonical_id: str
    aliases: list[str] = field(default_factory=list)
    confidence: float = 1.0
    needs_review: bool = False
    invalid: bool = False


@dataclass
class ModelParts:
    """모델코드 분해 결과."""

    model_name: str
    grade_suffix: str | None
    region: str | None


_MODEL_RE = re.compile(r"^(?P<name>[A-Z][A-Z0-9]{2,18})(?:\.(?P<suffix>[A-Z0-9.]+))?$")


# --- Parts ---------------------------------------------------------------


def resolve_parts(raw_part_nos: Iterable[str]) -> list[ResolvedEntity]:
    """부품번호 정규화 + dedup. part_no는 보통 정확 일치."""
    groups: dict[str, list[str]] = {}
    for raw in raw_part_nos:
        if raw is None or not str(raw).strip():
            continue
        canonical = axioms.normalize_part_no(str(raw))
        groups.setdefault(canonical, [])
        if str(raw) not in groups[canonical]:
            groups[canonical].append(str(raw))

    out: list[ResolvedEntity] = []
    for canonical, aliases in groups.items():
        invalid = not axioms.validate_part_no(canonical)
        out.append(
            ResolvedEntity(
                canonical_id=canonical,
                aliases=aliases,
                confidence=0.7 if invalid else 1.0,
                needs_review=invalid,
                invalid=invalid,
            )
        )
    return out


# --- Models --------------------------------------------------------------


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


def resolve_models(raw_model_codes: Iterable[str]) -> list[ResolvedEntity]:
    groups: dict[str, list[str]] = {}
    for raw in raw_model_codes:
        if raw is None or not str(raw).strip():
            continue
        canonical = axioms.normalize_model_code(str(raw))
        groups.setdefault(canonical, [])
        if str(raw) not in groups[canonical]:
            groups[canonical].append(str(raw))
    out: list[ResolvedEntity] = []
    for canonical, aliases in groups.items():
        invalid = not axioms.validate_model_code(canonical)
        out.append(
            ResolvedEntity(
                canonical_id=canonical,
                aliases=aliases,
                confidence=0.7 if invalid else 1.0,
                needs_review=invalid,
                invalid=invalid,
            )
        )
    return out


# --- Suppliers (fuzzy, 3-band) ------------------------------------------


def resolve_suppliers(raw_suppliers: Iterable[str]) -> list[ResolvedEntity]:
    """공급사명 3-band fuzzy 매칭. 약어/풀네임/한영 혼용 대응."""
    cleaned: list[str] = []
    for s in raw_suppliers:
        if s is None:
            continue
        t = unicodedata.normalize("NFC", str(s)).strip()
        if t:
            cleaned.append(t)

    canonical_names: list[str] = []
    out: list[ResolvedEntity] = []

    for name in cleaned:
        match = (
            process.extractOne(name, canonical_names, scorer=fuzz.token_sort_ratio)
            if canonical_names
            else None
        )
        if match is None or match[1] / 100.0 < REVIEW_LOW:
            canonical_names.append(name)
            out.append(ResolvedEntity(canonical_id=name, aliases=[name], confidence=1.0))
            continue
        score = match[1] / 100.0
        canonical = match[0]
        if score >= AUTO_MERGE:
            for ent in out:
                if ent.canonical_id == canonical:
                    if name not in ent.aliases:
                        ent.aliases.append(name)
                    break
        else:
            out.append(
                ResolvedEntity(
                    canonical_id=canonical,
                    aliases=[name],
                    confidence=score,
                    needs_review=True,
                )
            )
    return out


# --- Part name (3-band) -------------------------------------------------


def resolve_part_names(raw_names: Iterable[str]) -> list[ResolvedEntity]:
    """부품명 3-band 매칭 (한국어 NFC 후 비교)."""
    cleaned: list[str] = []
    for n in raw_names:
        if n is None:
            continue
        t = unicodedata.normalize("NFC", str(n)).strip()
        if t:
            cleaned.append(t)

    canonical: list[str] = []
    out: list[ResolvedEntity] = []
    for name in cleaned:
        match = process.extractOne(name, canonical, scorer=fuzz.ratio) if canonical else None
        if match is None or match[1] / 100.0 < REVIEW_LOW:
            canonical.append(name)
            out.append(ResolvedEntity(canonical_id=name, aliases=[name]))
            continue
        score = match[1] / 100.0
        if score >= AUTO_MERGE:
            for ent in out:
                if ent.canonical_id == match[0]:
                    if name not in ent.aliases:
                        ent.aliases.append(name)
                    break
        else:
            out.append(
                ResolvedEntity(
                    canonical_id=match[0],
                    aliases=[name],
                    confidence=score,
                    needs_review=True,
                )
            )
    return out
