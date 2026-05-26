"""v2.0 §7-3 — Hybrid 검색 파이프라인 (Router → Dense + Sparse → RRF → Rerank → Graph).

preprocessing_v2.md §7. 전체 흐름:
  1) Query Router → SearchPlan
  2) exact_sql → 식별자 매치는 SQL 직진
  3) dense (multi-vector) + sparse (pg_trgm) 각각 top-30
  4) RRF 결합
  5) bge-reranker로 top-5
  6) Graph expansion (BOM + 변경 체인 1-hop)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import ChangeEvent
from src.embed.embedder import embed_texts, embedding_enabled
from src.embed.reranker import RerankCandidate, rerank
from src.search.exact_sql import exact_search
from src.search.graph_expansion import expand_with_graph
from src.search.multi_vector_search import (
    multi_vector_search,
    single_vector_search,
)
from src.search.router import SearchPlan, retrieval_params, route_query
from src.search.trigram_search import trigram_search
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class SearchHit:
    """최종 검색 결과 (rerank 후, graph expansion 포함)."""

    event_id: str
    score: float
    change_point: str | None = None
    change_reason: str | None = None
    narrative_text: str | None = None
    form_version: str | None = None
    part_no: str | None = None
    new_model_code: str | None = None
    payload: dict[str, Any] | None = None
    graph_neighbors: list[dict[str, str]] = field(default_factory=list)


# ── RRF ────────────────────────────────────────────────────────────


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    rrf_k: int = 60,
) -> list[tuple[str, float]]:
    """여러 ranking을 RRF로 결합. score = Σ 1/(k + rank)."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (rrf_k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


# ── Main search ────────────────────────────────────────────────────


def _hydrate(session: Session, event_ids: list[str]) -> dict[str, ChangeEvent]:
    if not event_ids:
        return {}
    rows = (
        session.execute(select(ChangeEvent).where(ChangeEvent.event_id.in_(event_ids)))
        .scalars()
        .all()
    )
    return {str(r.event_id): r for r in rows}


def search(
    session: Session,
    query: str,
    top_k: int = 5,
    form_version: str | None = None,
    plan: SearchPlan | None = None,
) -> list[SearchHit]:
    """엔드투엔드 검색."""
    plan = plan or route_query(query)
    log.info("search.plan", case=plan.case_name, mode=plan.mode)
    params = retrieval_params()
    dense_k = int(params.get("dense_candidates", 30))
    sparse_k = int(params.get("sparse_candidates", 30))
    rrf_k = int(params.get("rrf_k", 60))
    rerank_top = int(params.get("rerank_top_k", top_k))

    # ── exact SQL 직진 ──
    if plan.mode == "exact_sql":
        hits = exact_search(session, plan.sql_filter_field or "part_no", plan.sql_filter_values, limit=top_k)
        return [_to_search_hit(session.get(ChangeEvent, h.event_id), h.score) for h in hits if h.event_id]

    # ── Dense retrieve ──
    dense_rank: list[str] = []
    if embedding_enabled():
        try:
            query_vec = embed_texts([query])[0]
        except Exception as exc:  # noqa: BLE001
            log.warning("search.embed_failed", error=str(exc))
            query_vec = None
        if query_vec is not None:
            if plan.mode.startswith("multi_vector"):
                dense_hits = multi_vector_search(
                    session,
                    query_vec,
                    plan.vector_weights or {plan.primary_vector: 1.0},
                    k=dense_k,
                    form_version=form_version,
                )
            else:
                dense_hits = single_vector_search(
                    session, plan.primary_vector, query_vec, k=dense_k, form_version=form_version
                )
            dense_rank = [h.event_id for h in dense_hits]

    # ── Sparse retrieve ──
    sparse_hits = trigram_search(session, query, k=sparse_k, form_version=form_version)
    sparse_rank = [h.event_id for h in sparse_hits]

    # ── exact_sql_plus_vector: SQL 필터 후보를 dense에 합쳐 부스트 ──
    if plan.mode == "exact_sql_plus_vector" and plan.sql_filter_values:
        exact_hits = exact_search(session, plan.sql_filter_field or "new_model_code", plan.sql_filter_values, limit=top_k)
        exact_rank = [h.event_id for h in exact_hits]
        rankings = [exact_rank, dense_rank, sparse_rank]
    else:
        rankings = [r for r in (dense_rank, sparse_rank) if r]

    if not rankings:
        return []
    fused = reciprocal_rank_fusion(rankings, rrf_k=rrf_k)
    candidate_ids = [eid for eid, _ in fused[: max(rerank_top * 3, top_k * 3)]]

    # ── Rerank ──
    hydrated = _hydrate(session, candidate_ids)
    candidates = [
        RerankCandidate(id=eid, text=ev.narrative_text or ev.change_point or "")
        for eid, ev in hydrated.items()
    ]
    reranked = rerank(query, candidates, top_k=rerank_top)

    # ── Graph expansion ──
    expansions: dict[str, list[dict[str, str]]] = {}
    if plan.expand_graph and reranked:
        neighbors = expand_with_graph(session, [r.id for r in reranked])
        for n in neighbors:
            expansions.setdefault(n.event_id, []).append(
                {"event_id": n.event_id, "relation": n.relation}
            )

    out: list[SearchHit] = []
    for r in reranked[:top_k]:
        ev = hydrated.get(r.id)
        if ev is None:
            continue
        hit = _to_search_hit(ev, r.score)
        hit.graph_neighbors = expansions.get(hit.event_id, [])
        out.append(hit)
    return out


def _to_search_hit(ev: ChangeEvent | None, score: float) -> SearchHit:
    if ev is None:
        return SearchHit(event_id="", score=score)
    return SearchHit(
        event_id=str(ev.event_id),
        score=score,
        change_point=ev.change_point,
        change_reason=ev.change_reason,
        narrative_text=ev.narrative_text,
        form_version=ev.form_version,
        part_no=ev.part_no,
        new_model_code=ev.new_model_code,
        payload=ev.payload,
    )
