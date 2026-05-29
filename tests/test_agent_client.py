"""UI 통합 — agent_client.analyze() 직렬화 테스트 (fake 백엔드 + SQLite)."""

from __future__ import annotations

import pytest

from src.agent.repository.bom import EdgeBomRepository
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import BomEdge, DevPartMaster, SourceFile
from src.db.retrieve import Hit
from src.ui.agent_client import analyze


@pytest.fixture()
def session(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 't.db'}")
    init_db(engine)
    SessionLocal = session_factory(engine)
    with SessionLocal() as s:
        yield s


class _Fake:
    def __init__(self, hits):
        self._hits = hits

    def search_changes(self, q, *, top_k=10, region=None):
        return list(self._hits)[:top_k]

    def hybrid_search(self, q, *, top_k=10, region=None):
        return []

    def lookup_by_attribute(self, *, top_k=20, **kw):
        return []

    def find_similar_changes(self, pno, *, top_k=5, region=None):
        return []


def test_analyze_serializes_full_view(session):
    sf = SourceFile(file_name="best.xlsx", file_hash="h1")
    session.add(sf)
    session.commit()
    seed = DevPartMaster(
        file_id=sf.file_id, form_id="changing_parts_list_96", part_no_new="AGG74419321",
        event="Change", sheet_name="list", source_row=5,
    )
    session.add(seed)
    session.add(DevPartMaster(file_id=sf.file_id, form_id="bom_ag_grid_36",
                              part_no_new="MFZ67394702", sheet_name="BOM", source_row=9))
    session.commit()
    session.add(BomEdge(file_id=sf.file_id, parent_pno="AGG74419321", child_pno="MFZ67394702"))
    session.commit()

    seed_hit = Hit(
        doc_id=seed.doc_id, part_no_new="AGG74419321", part_name="Packing", new_model="M",
        event="Change", region="EUR", form_id="changing_parts_list_96", file_id=sf.file_id,
        embedding_text="t", change_point_raw="재질", change_reason_raw="r",
    )
    res = analyze(
        "AGG74419321 변경", session=session, backend=_Fake([seed_hit]),
        bom_repo=EdgeBomRepository(session),
    )

    assert set(res) >= {"intent", "seeds", "tree", "verdicts", "doc", "violations", "tool_calls"}
    assert res["intent"]["part_nos"] == ["AGG74419321"]
    assert any(s["part_no"] == "AGG74419321" for s in res["seeds"])
    assert all(s["src"].startswith("[SRC ") for s in res["seeds"])
    assert {"changed_parts", "dev_master_rows", "bom_diff", "checklist"} <= set(res["doc"])
    assert res["doc"]["changed_parts"]
    assert all(r["src"].startswith("[SRC ") for r in res["doc"]["changed_parts"])
    assert res["violations"] == []
