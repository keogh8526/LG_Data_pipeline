"""Phase 3 — L2 Orchestrator 테스트.

retrieval 백엔드는 fake 주입(네트워크/임베딩 없음). walk_subtree + tool_call_log는
실제 SQLite. 검증: 교차쿼리 RRF 융합/dedup, reflection 트리거·조기종료, 직렬 트리 확장,
find_similar 보강, tool_call_log 기록.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from src.agent.intent.models import ChangeIntent
from src.agent.orchestrator import orchestrate
from src.agent.repository.bom import EdgeBomRepository
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import BomEdge, SourceFile, ToolCallLog
from src.db.retrieve import Hit


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


def _hit(doc_id: int, pno: str, model: str = "M1", file_id: int = 1) -> Hit:
    return Hit(
        doc_id=doc_id,
        part_no_new=pno,
        part_name=pno,
        new_model=model,
        event="Change",
        region="EUR",
        form_id="changing_parts_list_96",
        file_id=file_id,
        embedding_text=f"narrative {pno}",
        change_point_raw="cp",
        change_reason_raw="rsn",
    )


class FakeBackend:
    def __init__(self, change_results=None, *, hybrid=None, lookup=None, similar=None):
        self.change_results = change_results or {}
        self.hybrid = hybrid or []
        self.lookup = lookup or []
        self.similar = similar or []

    def search_changes(self, query, *, top_k=10, region=None):
        return list(self.change_results.get(query, []))[:top_k]

    def hybrid_search(self, query, *, top_k=10, region=None):
        return list(self.hybrid)[:top_k]

    def lookup_by_attribute(self, *, top_k=20, **kw):
        return list(self.lookup)[:top_k]

    def find_similar_changes(self, seed_pno, *, top_k=5, region=None):
        return list(self.similar)[:top_k]


def _intent(queries: list[str]) -> ChangeIntent:
    return ChangeIntent(
        raw_text="패킹 변경", rewritten_queries=queries, confidence=0.6, source="regex"
    )


def test_fuse_and_dedup(session):
    backend = FakeBackend(
        {
            "q1": [_hit(1, "A"), _hit(2, "B")],
            "q2": [_hit(2, "B"), _hit(3, "C")],  # B는 두 쿼리에 등장 → RRF 최상위
        }
    )
    res = orchestrate(_intent(["q1", "q2"]), session=session, backend=backend)
    assert {s.part_no_new for s in res.seeds} == {"A", "B", "C"}
    assert res.seeds[0].part_no_new == "B"  # 두 쿼리 등장 → 최상위
    assert res.reflections == 0


def test_reflection_triggers_when_weak(session):
    backend = FakeBackend(
        {"q1": [_hit(1, "A")]},  # 1개 < min_seeds(3)
        hybrid=[_hit(2, "B"), _hit(3, "C")],  # reflection 0회차에 보강
    )
    res = orchestrate(_intent(["q1"]), session=session, backend=backend, min_seeds=3)
    assert res.reflections == 1
    assert {s.part_no_new for s in res.seeds} == {"A", "B", "C"}


def test_reflection_early_stop_when_no_improvement(session):
    backend = FakeBackend({"q1": [_hit(1, "A")]})  # reflection이 아무것도 못 더함
    res = orchestrate(_intent(["q1"]), session=session, backend=backend, min_seeds=3)
    assert res.reflections == 1  # 1회 시도 후 개선 없음 → 조기 종료
    assert {s.part_no_new for s in res.seeds} == {"A"}


def test_walk_subtree_builds_tree(session):
    sf = SourceFile(file_name="bom.xlsx", file_hash="h1")
    session.add(sf)
    session.commit()
    for p, c in [("A", "X"), ("A", "Y")]:
        session.add(BomEdge(file_id=sf.file_id, parent_pno=p, child_pno=c))
    session.commit()

    backend = FakeBackend({"q1": [_hit(1, "A", file_id=sf.file_id)]})
    res = orchestrate(
        _intent(["q1"]),
        session=session,
        backend=backend,
        bom_repo=EdgeBomRepository(session),
        min_seeds=1,
    )
    assert {t.node.pno for t in res.tree if t.seed_pno == "A"} == {"X", "Y"}


def test_find_similar_augments_seeds(session):
    backend = FakeBackend({"q1": [_hit(1, "A")]}, similar=[_hit(9, "D")])
    res = orchestrate(_intent(["q1"]), session=session, backend=backend, min_seeds=1)
    assert "D" in {s.part_no_new for s in res.seeds}


def test_tool_call_log_recorded(session):
    backend = FakeBackend({"q1": [_hit(1, "A")], "q2": [_hit(2, "B")]})
    res = orchestrate(_intent(["q1", "q2"]), session=session, backend=backend, min_seeds=1)
    logged = session.execute(select(func.count()).select_from(ToolCallLog)).scalar_one()
    assert logged == res.tool_calls
    names = {
        r.tool_name
        for r in session.execute(select(ToolCallLog)).scalars().all()
    }
    assert "search_changes" in names
    assert "find_similar_changes" in names
    assert "walk_subtree" in names


def test_walk_is_cross_bom_not_seed_file(session):
    # 회귀: 엣지는 BOM 파일(sf1) 출처, seed Hit는 변경 파일(sf2) 출처.
    # walk을 seed.file_id로 스코핑하면 빈 트리 → file_id=None(교차 BOM)으로 찾아야 함.
    sf1 = SourceFile(file_name="bom.xlsx", file_hash="hb")
    sf2 = SourceFile(file_name="change.xlsx", file_hash="hc")
    session.add_all([sf1, sf2])
    session.commit()
    for p, c in [("A", "X"), ("A", "Y")]:
        session.add(BomEdge(file_id=sf1.file_id, parent_pno=p, child_pno=c))
    session.commit()

    backend = FakeBackend({"q1": [_hit(1, "A", file_id=sf2.file_id)]})
    res = orchestrate(
        _intent(["q1"]),
        session=session,
        backend=backend,
        bom_repo=EdgeBomRepository(session),
        min_seeds=1,
    )
    assert {t.node.pno for t in res.tree} == {"X", "Y"}
