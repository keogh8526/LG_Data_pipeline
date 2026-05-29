"""L2 retrieval 백엔드 (도구 4개의 데이터 접근).

``RetrievalBackend`` 프로토콜로 추상화 — 테스트는 fake 주입(네트워크/DB 없이),
운영은 ``DbRetrievalBackend``(session_factory 기반, 병렬 호출 시 호출당 fresh 세션).

- search_changes / hybrid_search: 기존 src/db/retrieve.py 재사용.
- lookup_by_attribute: dev_part_master 속성 WHERE (파라미터화 ORM).
- find_similar_changes: seed 부품의 변경 텍스트로 유사 변경 검색. 구조 B(change_event)
  적재 시 seed의 event_id 공유 라인 병합 예정 — 현재는 벡터/lexical 유사도만(gate).
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.db.models import DevPartMaster
from src.db.retrieve import Hit, hybrid_search, search_changes


def _dpm_to_hit(r: DevPartMaster) -> Hit:
    return Hit(
        doc_id=r.doc_id,
        part_no_new=r.part_no_new,
        part_name=r.part_name,
        new_model=r.new_model,
        event=r.event,
        region=r.region,
        form_id=r.form_id,
        file_id=r.file_id,
        embedding_text=r.embedding_text,
        part_no_base=r.part_no_base,
        change_point_raw=r.change_point_raw,
        change_reason_raw=r.change_reason_raw,
    )


class RetrievalBackend(Protocol):
    """L2 retrieval 도구 인터페이스 (스레드 세이프 구현 권장 — 병렬 호출됨)."""

    def search_changes(self, query: str, *, top_k: int = 10, region: str | None = None) -> list[Hit]: ...

    def hybrid_search(self, query: str, *, top_k: int = 10, region: str | None = None) -> list[Hit]: ...

    def lookup_by_attribute(
        self,
        *,
        top_k: int = 20,
        supplier: str | None = None,
        classification: str | None = None,
        part_type: str | None = None,
        region: str | None = None,
        new_model: str | None = None,
    ) -> list[Hit]: ...

    def find_similar_changes(
        self, seed_pno: str, *, top_k: int = 5, region: str | None = None
    ) -> list[Hit]: ...


class DbRetrievalBackend:
    """Postgres 백엔드. 호출당 fresh 세션(session_factory) → 병렬 안전."""

    _BOM_FORM_ID = "bom_ag_grid_36"

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._sf = session_factory

    def search_changes(self, query: str, *, top_k: int = 10, region: str | None = None) -> list[Hit]:
        with self._sf() as s:
            return search_changes(s, query, top_k=top_k, region=region)

    def hybrid_search(self, query: str, *, top_k: int = 10, region: str | None = None) -> list[Hit]:
        with self._sf() as s:
            return hybrid_search(s, query, top_k=top_k, region=region)

    def lookup_by_attribute(
        self,
        *,
        top_k: int = 20,
        supplier: str | None = None,
        classification: str | None = None,
        part_type: str | None = None,
        region: str | None = None,
        new_model: str | None = None,
    ) -> list[Hit]:
        with self._sf() as s:
            stmt = select(DevPartMaster)
            if supplier:
                stmt = stmt.where(DevPartMaster.supplier == supplier)
            if classification:
                stmt = stmt.where(DevPartMaster.classification == classification)
            if part_type:
                stmt = stmt.where(DevPartMaster.part_type == part_type)
            if region:
                stmt = stmt.where(DevPartMaster.region == region)
            if new_model:
                stmt = stmt.where(DevPartMaster.new_model == new_model)
            rows = s.execute(stmt.limit(top_k)).scalars().all()
            return [_dpm_to_hit(r) for r in rows]

    def find_similar_changes(
        self, seed_pno: str, *, top_k: int = 5, region: str | None = None
    ) -> list[Hit]:
        with self._sf() as s:
            seed = (
                s.execute(
                    select(DevPartMaster)
                    .where(DevPartMaster.part_no_new == seed_pno)
                    .where(DevPartMaster.form_id != self._BOM_FORM_ID)
                    .limit(1)
                )
                .scalars()
                .first()
            )
            if seed is None:
                return []
            text_query = (seed.change_point_raw or "") + " " + (seed.change_reason_raw or "")
            text_query = text_query.strip()
            if not text_query:
                return []
            hits = search_changes(s, text_query, top_k=top_k + 1, region=region)
            # TODO(구조 B): change_event 적재 후 seed.event_id 공유 라인 병합.
            return [h for h in hits if h.part_no_new != seed_pno][:top_k]
