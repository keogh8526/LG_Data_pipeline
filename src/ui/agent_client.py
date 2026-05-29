"""agent_client — UI 어댑터 (L1~L4 에이전트).

rag_client(단발 검색)와 같은 위치의 어댑터. UI는 ``run_change_analysis(text)`` 하나만
호출 → 구조화(L1)/회수+트리(L2)/영향 판정(L3)/문서(L4)를 직렬화 dict로 반환.

rag_client와 동일하게 ENABLE_EMBEDDING을 자동 활성화(검색 필수). LLM(L1 의미 슬롯/L4
설명)은 ENABLE_LLM=1일 때만 — 기본은 결정론 경로.
"""

from __future__ import annotations

import os
from typing import Any

# rag_client와 동일: 검색에 임베딩 필수 → 자동 활성화.
os.environ.setdefault("ENABLE_EMBEDDING", "1")

from sqlalchemy.orm import Session  # noqa: E402

from src.agent.docgen import validate_doc  # noqa: E402
from src.agent.docgen.generator import DocRow, source_ref_for  # noqa: E402
from src.agent.orchestrator.backend import DbRetrievalBackend, RetrievalBackend  # noqa: E402
from src.agent.pipeline import AgentOutput, run_agent  # noqa: E402
from src.agent.repository.bom import BomRepository, EdgeBomRepository  # noqa: E402
from src.db.engine import make_engine, session_factory  # noqa: E402

_ENGINE: Any = None
_SF: Any = None


def _factory() -> Any:
    """Engine + sessionmaker 캐시 (rag_client와 동일 패턴)."""
    global _ENGINE, _SF
    if _ENGINE is None:
        _ENGINE = make_engine()
        _SF = session_factory(_ENGINE)
    return _SF


def _doc_rows(rows: list[DocRow]) -> list[dict[str, Any]]:
    return [
        {
            "pno": r.pno_display,
            "is_new": r.is_new,
            "action": r.action,
            "tier": r.tier,
            "src": r.src,
            "detail": r.detail,
            "relation": r.relation,
        }
        for r in rows
    ]


def serialize(out: AgentOutput, session: Session) -> dict[str, Any]:
    """AgentOutput → UI 렌더용 dict."""
    ci = out.intent
    verdict_by = {v.part_no: v for v in out.verdicts}
    seeds: list[dict[str, Any]] = []
    for s in out.orchestration.seeds:
        v = verdict_by.get(s.part_no_new or "")
        seeds.append(
            {
                "part_no": s.part_no_new,
                "model": s.new_model,
                "event": s.event,
                "score_rrf": round(s.score_rrf, 4) if s.score_rrf is not None else None,
                "action": v.action if v else None,
                "tier": v.tier if v else None,
                "src": source_ref_for(session, s.doc_id).tag(),
            }
        )
    doc = out.doc
    return {
        "intent": {
            "source": ci.source,
            "confidence": round(ci.confidence, 3),
            "part_nos": ci.part_nos,
            "models": ci.models,
            "region": ci.region,
            "change_attribute": ci.change_attribute,
            "rewritten_queries": ci.rewritten_queries,
        },
        "seeds": seeds,
        "tree": [
            {"seed": t.seed_pno, "pno": t.node.pno, "depth": t.node.depth}
            for t in out.orchestration.tree
        ],
        "verdicts": [
            {
                "part_no": v.part_no,
                "action": v.action,
                "tier": v.tier,
                "rules": [f.rule_id for f in v.findings],
            }
            for v in out.verdicts
        ],
        "doc": {
            "changed_parts": _doc_rows(doc.changed_parts),
            "dev_master_rows": _doc_rows(doc.dev_master_rows),
            "bom_diff": _doc_rows(doc.bom_diff),
            "checklist": doc.checklist,
        },
        "violations": validate_doc(doc),
        "tool_calls": out.orchestration.tool_calls,
        "reflections": out.orchestration.reflections,
        "session_id": out.orchestration.session_id,
    }


def analyze(
    text: str,
    *,
    session: Session,
    backend: RetrievalBackend,
    bom_repo: BomRepository | None = None,
    llm: Any = None,
) -> dict[str, Any]:
    """주입된 session/backend로 L1~L4 실행 후 직렬화 (테스트는 fake 주입)."""
    out = run_agent(text, session=session, backend=backend, bom_repo=bom_repo, llm=llm)
    return serialize(out, session)


def run_change_analysis(text: str) -> dict[str, Any]:
    """UI 표준 진입점 — 실 Postgres + Ollama bge-m3로 L1~L4 실행."""
    factory = _factory()
    backend = DbRetrievalBackend(factory)
    with factory() as s:
        return analyze(text, session=s, backend=backend, bom_repo=EdgeBomRepository(s))


__all__ = ["analyze", "run_change_analysis", "serialize"]
