"""L2 orchestrator 본체.

흐름: rewritten_queries에 대해 search_changes 병렬 retrieval(asyncio) → RRF(k=60)
교차쿼리 융합 → dedup(new_pno, model_or_form) → 후보 약하면 reflection(≤2, 개선 없으면
조기 종료) → seed별 walk_subtree(직렬, seed 의존). 모든 도구 호출 tool_call_log 기록.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar
from uuid import uuid4

from sqlalchemy.orm import Session

from src.agent.intent.models import ChangeIntent
from src.agent.orchestrator.backend import RetrievalBackend
from src.agent.repository.bom import BomNode, BomRepository, EdgeBomRepository
from src.db.models import ToolCallLog
from src.db.retrieve import Hit
from src.utils.logging import get_logger

log = get_logger(__name__)

_RRF_K = 60
_T = TypeVar("_T")


@dataclass
class LogRec:
    tool_name: str
    arguments: dict[str, Any]
    result_count: int | None
    latency_ms: int
    status: str
    error: str | None


@dataclass
class TreeHit:
    seed_pno: str
    node: BomNode


@dataclass
class OrchestratorResult:
    intent: ChangeIntent
    seeds: list[Hit]
    tree: list[TreeHit]
    reflections: int
    tool_calls: int
    session_id: str
    trace: list[LogRec] = field(default_factory=list)


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _timed(
    name: str, args: dict[str, Any], fn: Callable[[], list[_T]]
) -> tuple[list[_T], LogRec]:
    """fn() 실행 + 타이밍/상태 기록. 예외는 status=error로 흡수(빈 결과)."""
    t0 = time.monotonic()
    try:
        result = fn()
        return result, LogRec(name, args, len(result), _ms(t0), "ok", None)
    except Exception as exc:  # noqa: BLE001 — 도구 실패는 trace에 남기고 계속
        log.warning("l2.tool_failed", tool=name, error=str(exc)[:160])
        return [], LogRec(name, args, 0, _ms(t0), "error", str(exc)[:500])


def _rrf_fuse(result_lists: list[list[Hit]], *, rrf_k: int = _RRF_K) -> list[Hit]:
    """교차쿼리 RRF: doc_id별 Σ 1/(k + rank + 1). score_rrf 세팅 후 desc 정렬."""
    merged: dict[int, Hit] = {}
    score: dict[int, float] = {}
    for hits in result_lists:
        for rank, h in enumerate(hits):
            merged.setdefault(h.doc_id, h)
            score[h.doc_id] = score.get(h.doc_id, 0.0) + 1.0 / (rrf_k + rank + 1)
    for doc_id, h in merged.items():
        h.score_rrf = score[doc_id]
    return sorted(merged.values(), key=lambda h: h.score_rrf or 0.0, reverse=True)


def _dedup(hits: list[Hit]) -> list[Hit]:
    """(new_pno, model_or_form) 기준 dedup — 동일 부품 중복 제거."""
    seen: set[tuple[str, str]] = set()
    out: list[Hit] = []
    for h in hits:
        key = ((h.part_no_new or "").strip().upper(), (h.new_model or h.form_id or ""))
        if key[0] and key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


async def _run_query_searches(
    backend: RetrievalBackend, queries: list[str], region: str | None, top_k: int
) -> list[tuple[list[Hit], LogRec]]:
    """rewritten_queries에 대해 search_changes 병렬 실행."""
    tasks = [
        asyncio.to_thread(
            _timed,
            "search_changes",
            {"query": q, "region": region},
            lambda q=q: backend.search_changes(q, top_k=top_k, region=region),
        )
        for q in queries
    ]
    return list(await asyncio.gather(*tasks))


def _reflect(
    backend: RetrievalBackend, intent: ChangeIntent, iteration: int
) -> list[tuple[list[Hit], LogRec]]:
    """후보 약할 때 확장: 0회차=raw_text hybrid_search, 1회차=속성 lookup."""
    if iteration == 0 and intent.raw_text:
        return [
            _timed(
                "hybrid_search",
                {"query": intent.raw_text},
                lambda: backend.hybrid_search(intent.raw_text, top_k=20, region=None),
            )
        ]
    return [
        _timed(
            "lookup_by_attribute",
            {"region": intent.region},
            lambda: backend.lookup_by_attribute(top_k=20, region=intent.region),
        )
    ]


def _write_logs(session: Session, session_id: str, recs: list[LogRec]) -> None:
    for r in recs:
        session.add(
            ToolCallLog(
                session_id=session_id,
                tool_name=r.tool_name,
                arguments=r.arguments,
                result_count=r.result_count,
                latency_ms=r.latency_ms,
                status=r.status,
                error_message=r.error,
            )
        )
    session.commit()


def orchestrate(
    intent: ChangeIntent,
    *,
    session: Session,
    backend: RetrievalBackend,
    bom_repo: BomRepository | None = None,
    session_id: str | None = None,
    max_seeds: int = 10,
    min_seeds: int = 3,
    max_reflection: int = 2,
    walk_depth: int = 2,
    per_query_top_k: int = 10,
) -> OrchestratorResult:
    """L1 ChangeIntent → seed 후보 + BOM 트리 부분집합. 모든 도구 호출 로깅."""
    repo = bom_repo or EdgeBomRepository(session)
    sid = session_id or uuid4().hex[:12]
    recs: list[LogRec] = []

    queries = intent.rewritten_queries or ([intent.raw_text] if intent.raw_text else [])
    result_lists: list[list[Hit]] = []
    if queries:
        for hits, rec in asyncio.run(
            _run_query_searches(backend, queries, intent.region, per_query_top_k)
        ):
            result_lists.append(hits)
            recs.append(rec)

    fused = _dedup(_rrf_fuse(result_lists))

    reflections = 0
    while len(fused) < min_seeds and reflections < max_reflection:
        prev = len(fused)
        for hits, rec in _reflect(backend, intent, reflections):
            result_lists.append(hits)
            recs.append(rec)
        fused = _dedup(_rrf_fuse(result_lists))
        reflections += 1
        if len(fused) <= prev:  # 개선 없음 → 조기 종료
            break

    seeds = fused[:max_seeds]

    # find_similar_changes로 top seed 보강 (도구 5개 중 하나 — 동시변경 신호)
    if seeds and seeds[0].part_no_new:
        sim, rec = _timed(
            "find_similar_changes",
            {"seed": seeds[0].part_no_new},
            lambda: backend.find_similar_changes(
                seeds[0].part_no_new or "", top_k=5, region=intent.region
            ),
        )
        recs.append(rec)
        seeds = _dedup(seeds + sim)[:max_seeds]

    # 트리 확장 — seed 의존이라 직렬.
    tree: list[TreeHit] = []
    for s in seeds:
        if not s.part_no_new:
            continue
        # file_id=None: seed(보통 변경리스트 파일 출처)와 bom_edge(BOM 파일 출처)의
        # file_id가 다르므로, 전체 BOM에서 seed 품번을 찾아 순회한다(교차 BOM).
        nodes, rec = _timed(
            "walk_subtree",
            {"seed": s.part_no_new, "depth": walk_depth},
            lambda s=s: repo.walk_subtree(  # type: ignore[misc]
                s.part_no_new or "", "down", walk_depth, None
            ),
        )
        recs.append(rec)
        tree.extend(TreeHit(seed_pno=s.part_no_new, node=n) for n in nodes)

    _write_logs(session, sid, recs)
    return OrchestratorResult(
        intent=intent,
        seeds=seeds,
        tree=tree,
        reflections=reflections,
        tool_calls=len(recs),
        session_id=sid,
        trace=recs,
    )
