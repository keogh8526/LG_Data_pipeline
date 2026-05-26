"""v2.0 §7-4 — Graph Expansion.

검색 top-K 결과의 인접 노드(BOM 1-hop + 변경 체인 1-hop)를 자동 회수.
"이 변경이 어디 영향?" 질의에 답할 수 있는 컨텍스트 자동 구성.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class GraphNeighbor:
    """1-hop 이웃."""

    event_id: str
    relation: str  # 'bom_sibling' / 'bom_parent' / 'bom_child' / 'change_chain_prev' / 'change_chain_next'
    distance: int = 1


_BOM_NEIGHBORS = """
SELECT DISTINCT ce.event_id::text AS event_id, 'bom_neighbor' AS relation
FROM change_events seed
JOIN bom_edges be
  ON be.child_part_no = seed.part_no
  OR be.parent_part_no = seed.part_no
JOIN change_events ce
  ON ce.part_no IN (be.parent_part_no, be.child_part_no)
WHERE seed.event_id = ANY(CAST(:ids AS uuid[]))
  AND ce.event_id <> seed.event_id
LIMIT :limit
"""

_CHANGE_CHAIN = """
SELECT DISTINCT ce.event_id::text AS event_id,
  CASE
    WHEN ce.part_no = seed.base_part_no THEN 'change_chain_prev'
    ELSE 'change_chain_next'
  END AS relation
FROM change_events seed
JOIN change_events ce
  ON ce.part_no = seed.base_part_no
  OR ce.base_part_no = seed.part_no
WHERE seed.event_id = ANY(CAST(:ids AS uuid[]))
  AND ce.event_id <> seed.event_id
LIMIT :limit
"""


def expand_with_graph(
    session: Session,
    seed_event_ids: Sequence[str],
    bom_limit: int = 20,
    chain_limit: int = 20,
) -> list[GraphNeighbor]:
    """seed event_ids의 BOM/change-chain 이웃 수집.

    Postgres 전용 (uuid[] 캐스팅). SQLite에서는 빈 결과 반환.
    """
    if not seed_event_ids:
        return []
    if session.bind is None or session.bind.dialect.name != "postgresql":
        return []

    out: list[GraphNeighbor] = []
    for sql, limit, default_rel in (
        (_BOM_NEIGHBORS, bom_limit, "bom_neighbor"),
        (_CHANGE_CHAIN, chain_limit, "change_chain"),
    ):
        try:
            result = session.execute(
                text(sql),
                {"ids": list(seed_event_ids), "limit": limit},
            )
            for row in result:
                out.append(
                    GraphNeighbor(event_id=str(row[0]), relation=str(row[1]))
                )
        except Exception:  # noqa: BLE001 — graph expansion 실패 시 RAG 자체는 진행
            continue
    return out
