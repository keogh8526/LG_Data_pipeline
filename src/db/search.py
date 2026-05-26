"""v2.0 — DB layer 검색 (얕은 SQL 헬퍼).

본격적인 RAG 검색은 ``src/search/pipeline.py``로 이전됨. 본 모듈은 CLI의
``search`` 명령이 기존 인터페이스를 유지하도록 얇은 래퍼만 제공한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.search.pipeline import SearchHit, search as _search


@dataclass
class HybridSearchResult:
    """레거시 인터페이스 호환용."""

    event_id: str
    score: float
    change_point: str | None = None
    form_version: str | None = None


def hybrid_search(
    session: Session,
    query: str,
    top_k: int = 10,
    form_version: str | None = None,
) -> list[SearchHit]:
    """`src.search.pipeline.search`로 위임."""
    return _search(session, query, top_k=top_k, form_version=form_version)
