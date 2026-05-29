"""Phase 2 — L1 Intent Structurizer 테스트.

LLM은 fake 주입(네트워크 없음). 검증: 정규식 선추출, 결정론 쿼리/confidence,
LLM 슬롯 병합, **잘못된 JSON 거부 → fallback**, self-consistency 과반, JSONB 캐시.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.agent.intent import ChangeIntent, cache_change_intent, structurize
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import DevPartMaster, SourceFile


class FakeLlm:
    """canned 응답을 순서대로 반환. Exception 항목은 raise (네트워크 실패 모사)."""

    def __init__(self, *responses: Any) -> None:
        self._responses = list(responses)
        self._i = 0

    def complete_json(self, prompt: str, *, system: str | None = None, temperature: float = 0.0):
        r = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


# ── 정규식 선추출 (LLM 없음) ───────────────────────────────────


def test_regex_extracts_part_no():
    ci = structurize("AGG74419321 내열 강화 패킹으로 변경", llm=None)
    assert "AGG74419321" in ci.part_nos
    assert ci.source == "regex"
    assert ci.rewritten_queries  # 결정론 쿼리 생성


def test_regex_extracts_model_with_dot():
    ci = structurize("WSED7667M.ABMQEUR 도어 힌지 변경", llm=None)
    assert any(m.startswith("WSED7667M") for m in ci.models)


def test_region_from_code_and_buyer():
    assert structurize("EUR 향 도어 변경", llm=None).region == "EUR"
    assert structurize("buyer LGESA 패킹 변경", llm=None).region == "SA"


def test_low_confidence_raw_fallback():
    ci = structurize("변경", llm=None)  # 엔티티 없음 → 낮은 confidence
    assert ci.source == "raw_fallback"
    assert ci.rewritten_queries == ["변경"]


def test_empty_text():
    ci = structurize("", llm=None)
    assert ci.part_nos == [] and ci.rewritten_queries == []


# ── LLM 슬롯 병합 / 스키마 거부 ────────────────────────────────


def test_llm_slots_merged():
    llm = FakeLlm(
        {
            "change_attribute": "재질",
            "change_direction": "대체",
            "intent_summary": "패킹 재질 변경",
            "rewritten_queries": ["내열 패킹 재질", "EPS 대체"],
            "confidence": 0.8,
        }
    )
    ci = structurize("AGG74419321 패킹 재질 변경", llm=llm)
    assert ci.source == "regex+llm"
    assert ci.change_attribute == "재질"
    assert ci.rewritten_queries == ["내열 패킹 재질", "EPS 대체"]
    assert ci.confidence == pytest.approx(0.8)
    assert "AGG74419321" in ci.part_nos  # 정규식 추출은 유지


def test_llm_bad_json_shape_rejected_falls_back():
    # rewritten_queries가 문자열(잘못된 타입) → ValidationError → fallback(regex)
    llm = FakeLlm({"rewritten_queries": "not-a-list", "confidence": 0.9})
    ci = structurize("AGG74419321 변경", llm=llm)
    assert ci.source == "regex"  # LLM 슬롯 거부됨
    assert "AGG74419321" in ci.part_nos


def test_llm_call_failure_falls_back():
    llm = FakeLlm(RuntimeError("ollama down"))
    ci = structurize("AGG74419321 변경", llm=llm)
    assert ci.source == "regex"


def test_self_consistency_majority():
    # 3회 중 2회 '재질' 일치, 1회 '치수' → 과반 '재질' 채택
    llm = FakeLlm(
        {"change_attribute": "재질", "confidence": 0.7, "rewritten_queries": ["a"]},
        {"change_attribute": "재질", "confidence": 0.9, "rewritten_queries": ["b"]},
        {"change_attribute": "치수", "confidence": 0.5, "rewritten_queries": ["c"]},
    )
    ci = structurize("AGG74419321 변경", llm=llm, self_consistency=3)
    assert ci.change_attribute == "재질"
    assert set(ci.rewritten_queries) == {"a", "b", "c"}  # 합집합


# ── 스키마 강제 ────────────────────────────────────────────────


def test_changeintent_rejects_bad_confidence_type():
    with pytest.raises(ValidationError):
        ChangeIntent(raw_text="x", confidence="높음")  # type: ignore[arg-type]


def test_confidence_clamped():
    assert ChangeIntent(raw_text="x", confidence=5.0).confidence == 1.0


# ── JSONB 캐시 ─────────────────────────────────────────────────


def test_cache_change_intent(session):
    sf = SourceFile(file_name="x.xlsx", file_hash="h1")
    session.add(sf)
    session.commit()
    dpm = DevPartMaster(file_id=sf.file_id, form_id="changing_parts_list_96", part_no_new="AGG74419321")
    session.add(dpm)
    session.commit()

    ci = structurize("AGG74419321 패킹 변경", llm=None)
    cache_change_intent(session, dpm.doc_id, ci)

    row = session.execute(select(DevPartMaster)).scalar_one()
    assert row.change_intent["part_nos"] == ["AGG74419321"]
    assert row.change_intent["source"] in ("regex", "raw_fallback")
