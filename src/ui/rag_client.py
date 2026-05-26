"""rag_client — 260508 UI 어댑터.

원본 (Azure OpenAI + Chroma) 인터페이스를 보존한 채 내부 구현을 D-012의
PostgreSQL/pgvector + Ollama bge-m3 + ``src.db.retrieve.hybrid_search``로
대체. UI 코드(app.py, chatbot_flow.py, feedback_chat.py)는 무수정으로 동작.

UI는 다음 두 함수만 호출:
  - retrieve_docs(query, top_k, filters)   → [{id, dist, meta, text}, ...]
  - get_collection().count()               → dev_part_master row count

쿼리 한 번 = hybrid_search(semantic + lexical RRF) 한 번.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

# ENABLE_EMBEDDING 자동 활성화 (UI에서 검색 시 필수)
os.environ.setdefault("ENABLE_EMBEDDING", "1")

from sqlalchemy.orm import Session  # noqa: E402

from src.db.engine import make_engine, session_factory  # noqa: E402
from src.db.models import DevPartMaster  # noqa: E402
from src.db.retrieve import hybrid_search  # noqa: E402


# -----------------------------
# 세션 캐시 (UI는 매 검색마다 호출)
# -----------------------------

_ENGINE = None
_SESSION_FACTORY = None


def _session() -> Session:
    """Engine + sessionmaker 캐시 후 새 Session 반환."""
    global _ENGINE, _SESSION_FACTORY
    if _ENGINE is None:
        _ENGINE = make_engine()
        _SESSION_FACTORY = session_factory(_ENGINE)
    return _SESSION_FACTORY()


# -----------------------------
# Collection adapter (count 등 메타 노출용)
# -----------------------------


class _CollectionAdapter:
    """Chroma의 col.count() 호환 wrapper. 다른 메서드는 필요 시 lazy 추가."""

    def __init__(self) -> None:
        self._cached_count: int | None = None

    def count(self) -> int:
        from sqlalchemy import func, select

        if self._cached_count is None:
            with _session() as s:
                self._cached_count = s.execute(
                    select(func.count()).select_from(DevPartMaster)
                ).scalar_one()
        return self._cached_count


_COLLECTION: _CollectionAdapter | None = None


def get_collection() -> _CollectionAdapter:
    """Chroma `get_collection()` 호환 — UI 디버그 사이드바가 count() 호출."""
    global _COLLECTION
    if _COLLECTION is None:
        _COLLECTION = _CollectionAdapter()
    return _COLLECTION


# -----------------------------
# Embedding (UI가 직접 호출하는 경우 대비)
# -----------------------------


def embed_query(text: str) -> List[float]:
    """단일 쿼리 임베딩 (bge-m3 via Ollama)."""
    from src.embed.embedder import embed_texts

    return embed_texts([text])[0]


# -----------------------------
# Filter 변환
# -----------------------------

# UI가 전달하는 Chroma-style filters → hybrid_search 인자 매핑.
# 우리 dev_part_master 컬럼명에 맞춰 일부 alias 처리.
_FILTER_ALIASES = {
    "model_prefix": None,   # Chroma 시절 메타. PostgreSQL에선 별도 처리 필요 (현재 무시).
    "product": None,        # 무시
    "platform": None,       # 무시
    "form_id": "form_id",
    "form_id_like": "form_id_like",
    "event": "event",
    "region": "region",
    "file_id": "file_id",
}


def _translate_filters(filters: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Chroma where → hybrid_search kwargs. 알 수 없는 키는 silently skip."""
    if not filters:
        return {}
    out: Dict[str, Any] = {}
    for k, v in filters.items():
        if v is None or v == "":
            continue
        target = _FILTER_ALIASES.get(k)
        if target is None:
            continue
        # list/tuple → 첫 값만 사용 (hybrid_search는 단일 필터). 멀티 필터는 후속.
        if isinstance(v, (list, tuple, set)):
            vv = [x for x in v if x not in (None, "")]
            if not vv:
                continue
            v = vv[0]
        out[target] = v
    return out


# -----------------------------
# Retrieve — UI 표준 인터페이스
# -----------------------------


def retrieve_docs(
    query: str,
    top_k: int = 5,
    filters: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """UI가 호출하는 표준 인터페이스.

    Args:
        query: 자연어 쿼리.
        top_k: 반환 행 수.
        filters: Chroma where 호환 dict. {form_id, event, region, file_id} 인식.

    Returns:
        [{id, dist, meta, text}, ...]
        - id: doc_id (str)
        - dist: 1 - score_rrf (RRF 점수의 inverse — 작을수록 더 관련)
        - meta: dev_part_master 컬럼 dict (part_no_new, part_name, new_model,
                event, region, form_id, file_id, score_rrf, score_semantic,
                score_lexical 등)
        - text: embedding_text (narrative)
    """
    kwargs = _translate_filters(filters)
    with _session() as s:
        hits = hybrid_search(s, query, top_k=int(top_k), **kwargs)

    out: List[Dict[str, Any]] = []
    for h in hits:
        meta = {
            "doc_id": h.doc_id,
            "part_no_new": h.part_no_new,
            "part_no": h.part_no_new,        # UI 호환 alias
            "part_name": h.part_name,
            "desc": h.part_name,             # UI 호환 alias
            "new_model": h.new_model,
            "base_model": None,              # 필요 시 별도 조회
            "event": h.event,
            "region": h.region,
            "form_id": h.form_id,
            "file_id": h.file_id,
            "score_rrf": h.score_rrf,
            "score_semantic": h.score_semantic,
            "score_lexical": h.score_lexical,
            "rank_semantic": h.rank_semantic,
            "rank_lexical": h.rank_lexical,
        }
        # dist = 거리 메트릭 (낮을수록 유사) — Chroma cosine distance와 의미 맞춤.
        # RRF는 점수(높을수록 좋음)라 1 - score_rrf로 변환.
        dist = 1.0 - (h.score_rrf or 0.0) if h.score_rrf is not None else None

        out.append(
            {
                "id": str(h.doc_id),
                "dist": dist,
                "meta": meta,
                "text": h.embedding_text or "",
            }
        )
    return out


__all__ = ["embed_query", "get_collection", "retrieve_docs"]
