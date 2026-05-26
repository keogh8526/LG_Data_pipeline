"""v2.0 (D-011 후) BOM Agent용 단순 검색 함수.

이전 src/search/ 패키지 (7-case Router, multi-vector RRF, bge-reranker,
graph expansion, context builder)는 D-011 Phase D에서 삭제. BOM Agent가
자체 retrieve 로직을 가지므로 SQL/벡터 헬퍼 1개로 충분.

사용 시나리오:
  1. BOM Agent의 retrieve 노드가 CP별로 본 함수를 호출.
  2. (pno + part_name + change_reason 키워드) 조합으로 ChangeEvent 검색.
  3. 결과는 source_file(file_id) 단위 집계해 후보 마스터로 사용.

검색 우선순위:
  - pno 있으면 part_no/base_part_no 정확 매치 우선 (top of result).
  - part_name_keyword 있으면 part_name ILIKE 매치.
  - change_reason_keyword 있으면 change_reason ILIKE 매치.
  - narrative_emb 존재 + query_emb 전달 시 cosine 유사도로 추가 ranking
    (현재 query_emb 인자 미사용 — 호출자가 별도 embed 후 BOM Agent에서 합칠 수도 있음).
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.db.models import ChangeEvent


def search_change_events(
    session: Session,
    pno: str | None = None,
    part_name_keyword: str | None = None,
    change_reason_keyword: str | None = None,
    top_k: int = 30,
) -> list[ChangeEvent]:
    """BOM Agent retrieve 노드가 호출하는 단순 검색.

    Args:
        session: 활성 SQLAlchemy session.
        pno: 부품번호 — part_no 또는 base_part_no 정확 매치.
        part_name_keyword: 부품명 부분 매치 (ILIKE / SQLite contains).
        change_reason_keyword: 변경사유 부분 매치.
        top_k: 결과 행 수 (기본 30).

    Returns:
        ChangeEvent ORM 객체 리스트. 매치 우선순위: pno > part_name > change_reason.
    """
    if not any([pno, part_name_keyword, change_reason_keyword]):
        return []

    filters = []
    if pno:
        filters.append(
            or_(ChangeEvent.part_no == pno, ChangeEvent.base_part_no == pno)
        )
    if part_name_keyword:
        like = f"%{part_name_keyword}%"
        filters.append(ChangeEvent.part_name.ilike(like))
    if change_reason_keyword:
        like = f"%{change_reason_keyword}%"
        filters.append(ChangeEvent.change_reason.ilike(like))

    stmt = select(ChangeEvent).where(or_(*filters)).limit(top_k)
    return list(session.execute(stmt).scalars().all())
