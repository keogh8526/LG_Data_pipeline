"""D-012 — dev_part_master 검색 모듈.

3 검색 모드:
  semantic_search   bge-m3 임베딩 cosine 거리 (HNSW 인덱스 사용)
  lexical_search    pg_trgm word_similarity (embedding_text의 GIN trgm 인덱스 사용)
  hybrid_search     RRF (Reciprocal Rank Fusion)로 위 둘을 결합

공통 필터: form_id / event / region / file_id / form_id_like.

호출 패턴 (BOM Agent retrieve 노드 또는 CLI):
    from src.db.retrieve import hybrid_search
    with Session() as s:
        hits = hybrid_search(s, '내열 강화 패킹 변경', top_k=10, event='Change')
        for h in hits:
            print(h.score_rrf, h.part_no_new, h.part_name)

ENABLE_EMBEDDING=1 필요 (Ollama bge-m3). lexical_search는 임베딩 없이 사용 가능.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from src.utils.logging import get_logger

log = get_logger(__name__)

# RRF 상수 — 표준값 60. 작을수록 top rank 가중 증가, 클수록 평탄화.
_RRF_K = 60

# 검색 후보 풀 크기 (각 모달리티별 raw top-N → RRF 후 top-k)
_DEFAULT_CANDIDATE_POOL = 30


@dataclass
class Hit:
    """검색 결과 1건. 세 모달리티 점수를 모두 노출 (디버깅 + UI에 도움)."""

    doc_id: int
    part_no_new: str | None
    part_name: str | None
    new_model: str | None
    event: str | None
    region: str | None
    form_id: str | None
    file_id: int
    embedding_text: str | None

    # 점수 (모달리티별, 없으면 None)
    score_semantic: float | None = None  # 1 - cosine_distance, 0~1
    score_lexical: float | None = None   # pg_trgm word_similarity, 0~1
    score_rrf: float | None = None       # RRF 융합, 보통 0~0.04

    # rank (모달리티별, 0-based)
    rank_semantic: int | None = None
    rank_lexical: int | None = None


# ---------------------------------------------------------------------------
# 내부 helpers
# ---------------------------------------------------------------------------


def _vec_str(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def _build_filter_sql(
    form_id: str | None = None,
    form_id_like: str | None = None,
    event: str | None = None,
    region: str | None = None,
    file_id: int | None = None,
) -> tuple[str, dict[str, Any]]:
    """필터 조건들을 WHERE clause로 묶음."""
    where: list[str] = []
    params: dict[str, Any] = {}
    if form_id:
        where.append("form_id = :form_id")
        params["form_id"] = form_id
    if form_id_like:
        where.append("form_id LIKE :form_id_like")
        params["form_id_like"] = form_id_like
    if event:
        where.append("event = :event")
        params["event"] = event
    if region:
        where.append("region = :region")
        params["region"] = region
    if file_id is not None:
        where.append("file_id = :file_id")
        params["file_id"] = file_id
    sql = (" AND " + " AND ".join(where)) if where else ""
    return sql, params


def _row_to_hit(row: Any, **score_kwargs: Any) -> Hit:
    return Hit(
        doc_id=row.doc_id,
        part_no_new=row.part_no_new,
        part_name=row.part_name,
        new_model=row.new_model,
        event=row.event,
        region=row.region,
        form_id=row.form_id,
        file_id=row.file_id,
        embedding_text=row.embedding_text,
        **score_kwargs,
    )


def _embed(query: str) -> list[float]:
    """쿼리 임베딩. ENABLE_EMBEDDING=1 + Ollama bge-m3 필요."""
    if os.environ.get("ENABLE_EMBEDDING", "0") != "1":
        raise RuntimeError(
            "Set ENABLE_EMBEDDING=1 and start Ollama bge-m3 for semantic search."
        )
    from src.embed.embedder import embed_texts

    return embed_texts([query])[0]


# ---------------------------------------------------------------------------
# Public retrievers
# ---------------------------------------------------------------------------


def semantic_search(
    session: Session,
    query: str,
    *,
    top_k: int = 10,
    form_id: str | None = None,
    form_id_like: str | None = None,
    event: str | None = None,
    region: str | None = None,
    file_id: int | None = None,
    query_vec: list[float] | None = None,
) -> list[Hit]:
    """벡터 cosine 거리 기준 검색 (HNSW 인덱스 사용).

    Args:
        query: 자연어 쿼리.
        top_k: 최종 반환 행 수.
        form_id / form_id_like / event / region / file_id: 메타 필터.
        query_vec: 외부에서 미리 임베딩한 벡터 (없으면 자동 임베딩).

    Returns:
        :class:`Hit` 리스트. score_semantic은 1 - cosine_distance, rank_semantic은 0-based.
    """
    vec = query_vec or _embed(query)
    flt_sql, params = _build_filter_sql(form_id, form_id_like, event, region, file_id)
    params["v"] = _vec_str(vec)
    params["k"] = top_k

    sql = text(
        f"""
        SELECT doc_id, part_no_new, part_name, new_model, event, region,
               form_id, file_id, embedding_text,
               1 - (embedding_dense <=> CAST(:v AS vector)) AS score
        FROM dev_part_master
        WHERE embedding_dense IS NOT NULL{flt_sql}
        ORDER BY embedding_dense <=> CAST(:v AS vector)
        LIMIT :k
        """
    )
    rows = session.execute(sql, params).all()
    return [
        _row_to_hit(r, score_semantic=float(r.score), rank_semantic=i)
        for i, r in enumerate(rows)
    ]


def lexical_search(
    session: Session,
    query: str,
    *,
    top_k: int = 10,
    form_id: str | None = None,
    form_id_like: str | None = None,
    event: str | None = None,
    region: str | None = None,
    file_id: int | None = None,
    min_similarity: float = 0.05,
) -> list[Hit]:
    """pg_trgm word_similarity 기준 검색 (embedding_text의 GIN trgm 인덱스 사용).

    Postgres 전용. SQLite는 미지원 (단위 테스트는 semantic만).

    Args:
        query: 키워드 또는 짧은 자연어.
        top_k: 최종 반환 행 수.
        min_similarity: 이 값 미만은 cutoff (잡음 제거).

    Returns:
        :class:`Hit` 리스트. score_lexical은 0~1, rank_lexical은 0-based.
    """
    flt_sql, params = _build_filter_sql(form_id, form_id_like, event, region, file_id)
    params["q"] = query
    params["mn"] = min_similarity
    params["k"] = top_k

    # word_similarity는 query가 text의 일부와 얼마나 닮았나. trgm GIN 인덱스가
    # `<%`(left-arg-word-sim) 연산자로 prefilter, similarity 정렬에 사용.
    sql = text(
        f"""
        SELECT doc_id, part_no_new, part_name, new_model, event, region,
               form_id, file_id, embedding_text,
               word_similarity(:q, embedding_text) AS score
        FROM dev_part_master
        WHERE embedding_text IS NOT NULL
          AND word_similarity(:q, embedding_text) >= :mn{flt_sql}
        ORDER BY word_similarity(:q, embedding_text) DESC
        LIMIT :k
        """
    )
    rows = session.execute(sql, params).all()
    return [
        _row_to_hit(r, score_lexical=float(r.score), rank_lexical=i)
        for i, r in enumerate(rows)
    ]


def hybrid_search(
    session: Session,
    query: str,
    *,
    top_k: int = 10,
    candidate_pool: int = _DEFAULT_CANDIDATE_POOL,
    rrf_k: int = _RRF_K,
    semantic_weight: float = 1.0,
    lexical_weight: float = 1.0,
    form_id: str | None = None,
    form_id_like: str | None = None,
    event: str | None = None,
    region: str | None = None,
    file_id: int | None = None,
) -> list[Hit]:
    """Semantic + Lexical RRF 융합.

    동작:
      1. semantic / lexical 각각 ``candidate_pool``(=30) 만큼 top-N 후보 수집.
      2. RRF 점수 = Σ weight_i / (rrf_k + rank_i)  (각 모달리티에 등장한 doc만 합)
      3. RRF 점수 desc 정렬 후 top_k 반환.

    Args:
        query: 자연어 쿼리.
        top_k: 최종 반환 행 수 (기본 10).
        candidate_pool: 각 모달리티 raw top-N (기본 30).
        rrf_k: RRF 상수 (기본 60; 낮을수록 top rank 가중↑).
        semantic_weight / lexical_weight: 모달리티 가중치 (1.0이 균등).
        그 외 필터: form_id 등.

    Returns:
        :class:`Hit` 리스트. score_rrf 정렬 desc. semantic/lexical 점수와
        rank도 동시 노출 — 디버깅·UI에 활용.
    """
    sem = semantic_search(
        session,
        query,
        top_k=candidate_pool,
        form_id=form_id,
        form_id_like=form_id_like,
        event=event,
        region=region,
        file_id=file_id,
    )
    lex = lexical_search(
        session,
        query,
        top_k=candidate_pool,
        form_id=form_id,
        form_id_like=form_id_like,
        event=event,
        region=region,
        file_id=file_id,
    )

    # doc_id → 통합 Hit dict
    merged: dict[int, Hit] = {}
    for h in sem:
        merged[h.doc_id] = h  # semantic Hit (lexical 정보는 아직 없음)
    for h in lex:
        if h.doc_id in merged:
            # 같은 doc — lexical 점수 추가
            cur = merged[h.doc_id]
            cur.score_lexical = h.score_lexical
            cur.rank_lexical = h.rank_lexical
        else:
            merged[h.doc_id] = h

    # RRF 점수
    for h in merged.values():
        rrf = 0.0
        if h.rank_semantic is not None:
            rrf += semantic_weight / (rrf_k + h.rank_semantic + 1)
        if h.rank_lexical is not None:
            rrf += lexical_weight / (rrf_k + h.rank_lexical + 1)
        h.score_rrf = rrf

    ranked = sorted(merged.values(), key=lambda h: h.score_rrf or 0.0, reverse=True)
    return ranked[:top_k]


__all__ = ["Hit", "hybrid_search", "lexical_search", "semantic_search"]
