"""L1 structurizer — 자유텍스트 → ChangeIntent.

흐름: 정규식 선추출(품번/모델/region — config/axioms.yaml 패턴 재사용, 결정론) →
로컬 LLM JSON 모드 의미 슬롯(게이트, self-consistency 옵션) → confidence 임계 미만이면
raw fallback. 결과는 dev_part_master.change_intent JSONB에 캐시 가능.

LLM 비활성(ENABLE_LLM!=1)이거나 실패해도 정규식 경로로 항상 ChangeIntent를 만든다.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from pydantic import ValidationError
from sqlalchemy.orm import Session

from src.agent.intent.models import ChangeIntent, IntentSource, LlmSlots
from src.agent.llm.client import LlmClient, default_llm, llm_enabled
from src.db.models import DevPartMaster
from src.ontology.axioms import (
    normalize_model_code,
    normalize_part_no,
    region_from_buyer,
    validate_model_code,
    validate_part_no,
    validate_region,
)
from src.utils.logging import get_logger

log = get_logger(__name__)

_DEFAULT_THRESHOLD = 0.35
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.@/()\-]*")
_STRIP = "./-()"

_SYSTEM = (
    "너는 LG 가전 부품 BOM 변경 설명에서 구조화된 변경 의도를 추출한다. "
    "반드시 JSON 객체만 반환한다. 품번을 새로 지어내지 않는다."
)


def _tokenize(text: str) -> list[str]:
    return [t.strip(_STRIP) for t in _TOKEN_RE.findall(text)]


def _extract_entities(text: str) -> tuple[list[str], list[str], str | None]:
    """정규식 선추출: (part_nos, models, region). 결정론, LLM 0회."""
    part_nos: list[str] = []
    models: list[str] = []
    region: str | None = None
    for tok in _tokenize(text):
        if not tok:
            continue
        if region is None:
            r = region_from_buyer(tok) or (tok.upper() if validate_region(tok) else None)
            if r:
                region = r
                continue
        if ("." in tok or "@" in tok) and validate_model_code(tok):
            norm = normalize_model_code(tok)
            if norm not in models:
                models.append(norm)
        elif validate_part_no(tok):
            norm = normalize_part_no(tok)
            if norm not in part_nos:
                part_nos.append(norm)
    return part_nos, models, region


def _deterministic_queries(
    text: str, part_nos: list[str], models: list[str], attribute: str | None
) -> list[str]:
    """LLM 없이 검색용 재작성 쿼리 생성 (3~4개)."""
    candidates: list[str] = []
    if text:
        candidates.append(text)
    if part_nos:
        candidates.append(f"{part_nos[0]} 변경 영향")
    if models and attribute:
        candidates.append(f"{models[0]} {attribute}")
    elif models:
        candidates.append(f"{models[0]} 변경")
    if attribute:
        candidates.append(f"{attribute} 변경 부품")
    out: list[str] = []
    for q in candidates:
        q = q.strip()
        if q and q not in out:
            out.append(q)
    return out[:4] or ([text] if text else [])


def _heuristic_confidence(
    part_nos: list[str], models: list[str], region: str | None
) -> float:
    conf = 0.25
    if part_nos:
        conf += 0.30
    if models:
        conf += 0.15
    if region:
        conf += 0.10
    return min(1.0, conf)


def _build_prompt(text: str, part_nos: list[str], models: list[str]) -> str:
    return (
        "다음 변경 설명을 분석해 JSON으로 반환하라.\n"
        f"설명: {text}\n"
        f"이미 추출된 품번: {part_nos}\n"
        f"이미 추출된 모델: {models}\n"
        "JSON 키:\n"
        "- change_attribute: 무엇이 바뀌나 (예: 재질/치수/공급처/UIT/색상). 모르면 null\n"
        "- change_direction: 증가/감소/대체/삭제/추가 중 하나 또는 null\n"
        "- intent_summary: 한 문장 한국어 요약\n"
        "- rewritten_queries: 검색용 재작성 쿼리 3~4개 (한국어 문자열 배열)\n"
        "- confidence: 0~1 실수\n"
    )


def _merge_slots(samples: list[LlmSlots]) -> LlmSlots:
    """self-consistency: 과반 일치 슬롯만 유지, 쿼리는 합집합, confidence는 평균."""
    n = len(samples)

    def majority(vals: list[str | None]) -> str | None:
        counts = Counter(v for v in vals if v)
        if not counts:
            return None
        val, cnt = counts.most_common(1)[0]
        return val if cnt * 2 >= n else None

    summaries = [s.intent_summary for s in samples if s.intent_summary]
    queries: list[str] = []
    for s in samples:
        for q in s.rewritten_queries:
            if q not in queries:
                queries.append(q)
    return LlmSlots(
        change_attribute=majority([s.change_attribute for s in samples]),
        change_direction=majority([s.change_direction for s in samples]),
        intent_summary=Counter(summaries).most_common(1)[0][0] if summaries else "",
        rewritten_queries=queries,
        confidence=sum(s.confidence for s in samples) / n,
    )


def _run_llm(
    llm: LlmClient, text: str, part_nos: list[str], models: list[str], n: int
) -> LlmSlots | None:
    prompt = _build_prompt(text, part_nos, models)
    samples: list[LlmSlots] = []
    runs = max(1, n)
    for _ in range(runs):
        temperature = 0.0 if runs == 1 else 0.4
        try:
            raw = llm.complete_json(prompt, system=_SYSTEM, temperature=temperature)
            samples.append(LlmSlots.model_validate(raw))
        except (ValidationError, ValueError) as exc:
            log.warning("l1.llm_schema_reject", error=str(exc)[:160])
        except Exception as exc:  # noqa: BLE001 — 네트워크 등 → fallback
            log.warning("l1.llm_call_failed", error=str(exc)[:160])
    if not samples:
        return None
    return _merge_slots(samples) if len(samples) > 1 else samples[0]


def structurize(
    raw_text: str,
    *,
    llm: LlmClient | None = None,
    confidence_threshold: float = _DEFAULT_THRESHOLD,
    self_consistency: int = 1,
) -> ChangeIntent:
    """자유텍스트 → ChangeIntent. ``llm=None``이면 ENABLE_LLM=1일 때만 LLM 사용."""
    text = unicodedata.normalize("NFC", raw_text or "").strip()
    part_nos, models, region = _extract_entities(text)

    if llm is None and llm_enabled():
        llm = default_llm()

    slots: LlmSlots | None = None
    if llm is not None and text:
        slots = _run_llm(llm, text, part_nos, models, self_consistency)

    source: IntentSource
    if slots is not None:
        attribute = slots.change_attribute
        direction = slots.change_direction
        summary = slots.intent_summary
        queries = slots.rewritten_queries or _deterministic_queries(
            text, part_nos, models, attribute
        )
        confidence = slots.confidence
        source = "regex+llm"
    else:
        attribute = direction = None
        summary = ""
        queries = _deterministic_queries(text, part_nos, models, None)
        confidence = _heuristic_confidence(part_nos, models, region)
        source = "regex"

    if confidence < confidence_threshold:
        source = "raw_fallback"
        queries = [text] if text else []

    return ChangeIntent(
        raw_text=text,
        part_nos=part_nos,
        models=models,
        region=region,
        change_attribute=attribute,
        change_direction=direction,
        intent_summary=summary,
        rewritten_queries=queries,
        confidence=confidence,
        source=source,
    )


def cache_change_intent(session: Session, doc_id: int, intent: ChangeIntent) -> None:
    """ChangeIntent를 dev_part_master.change_intent JSONB에 캐시."""
    row = session.get(DevPartMaster, doc_id)
    if row is None:
        raise ValueError(f"unknown doc_id={doc_id}")
    row.change_intent = intent.model_dump()
    session.commit()
