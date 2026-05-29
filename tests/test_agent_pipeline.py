"""Phase 6 — E2E 한 경로 (L1~L4) 통합 테스트.

retrieval은 fake 백엔드. walk_subtree / 출처 해소 / tool_call_log는 실제 SQLite.
검증: 자유텍스트 → seed + 트리 → 영향 판정 → 문서 3종 + 출처/품번 검증 통과.
"""

from __future__ import annotations

import pytest

from src.agent.docgen import validate_doc
from src.agent.pipeline import run_agent
from src.agent.repository.bom import EdgeBomRepository
from src.db.engine import init_db, make_engine, session_factory
from src.db.models import BomEdge, DevPartMaster, SourceFile
from src.db.retrieve import Hit


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


def test_e2e_l1_to_l4(session):
    sf = SourceFile(file_name="best.xlsx", file_hash="h1")
    session.add(sf)
    session.commit()
    seed = DevPartMaster(
        file_id=sf.file_id, form_id="changing_parts_list_96", part_no_new="AGG74419321",
        event="Change", sheet_name="변경부품 list", source_row=5,
    )
    session.add(seed)
    for child, row in [("MFZ67394702", 10), ("MAB62761349", 11)]:
        session.add(
            DevPartMaster(
                file_id=sf.file_id, form_id="bom_ag_grid_36", part_no_new=child,
                sheet_name="BOM", source_row=row,
            )
        )
    session.commit()
    for child in ["MFZ67394702", "MAB62761349"]:
        session.add(BomEdge(file_id=sf.file_id, parent_pno="AGG74419321", child_pno=child))
    session.commit()

    seed_hit = Hit(
        doc_id=seed.doc_id, part_no_new="AGG74419321", part_name="Packing",
        new_model="WSED7667M", event="Change", region="EUR",
        form_id="changing_parts_list_96", file_id=sf.file_id,
        embedding_text="패킹 재질 변경", change_point_raw="재질", change_reason_raw="규제",
    )
    out = run_agent(
        "AGG74419321 패킹 재질 변경",
        session=session,
        backend=_Fake([seed_hit]),
        bom_repo=EdgeBomRepository(session),
    )

    # L1
    assert "AGG74419321" in out.intent.part_nos
    # L2: seed + 트리
    assert "AGG74419321" in {s.part_no_new for s in out.orchestration.seeds}
    assert {"MFZ67394702", "MAB62761349"} <= {t.node.pno for t in out.orchestration.tree}
    # L3
    by = {v.part_no: v for v in out.verdicts}
    assert by["AGG74419321"].action == "MODIFY"   # event=Change
    assert by["MFZ67394702"].action == "CHECK"    # child (attribute 미상 → 보수적)
    # L4: 문서 3종 + 검증 통과 (모든 행 출처 보유, NEW 없음)
    assert out.doc.changed_parts
    assert out.doc.bom_diff
    assert validate_doc(out.doc) == []
    assert all(r.src.startswith("[SRC ") and r.valid_source for r in out.doc.changed_parts)


class _FakeLlm:
    def complete_json(self, prompt, *, system=None, temperature=0.0):
        return {
            "change_attribute": "재질",
            "change_direction": None,
            "intent_summary": "재질 변경",
            "rewritten_queries": ["재질 변경"],
            "confidence": 0.8,
        }


def test_e2e_downward_gate_uses_child_part_type(session):
    """하향 게이트: 변경속성=재질일 때 사출 자식=CHECK(관련), 전장 자식=KEEP(무관)."""
    sf = SourceFile(file_name="best.xlsx", file_hash="h1")
    session.add(sf)
    session.commit()
    seed = DevPartMaster(
        file_id=sf.file_id, form_id="changing_parts_list_96", part_no_new="AGG74419321",
        event="Change", sheet_name="list", source_row=5,
    )
    session.add(seed)
    session.add(DevPartMaster(file_id=sf.file_id, form_id="bom_ag_grid_36",
                              part_no_new="REL11110000", part_type="사출", sheet_name="BOM", source_row=2))
    session.add(DevPartMaster(file_id=sf.file_id, form_id="bom_ag_grid_36",
                              part_no_new="UNR22220000", part_type="전장", sheet_name="BOM", source_row=3))
    session.commit()
    for child in ["REL11110000", "UNR22220000"]:
        session.add(BomEdge(file_id=sf.file_id, parent_pno="AGG74419321", child_pno=child))
    session.commit()

    seed_hit = Hit(
        doc_id=seed.doc_id, part_no_new="AGG74419321", part_name="P", new_model="M",
        event="Change", region="EUR", form_id="changing_parts_list_96",
        file_id=sf.file_id, embedding_text="t", change_point_raw="재질", change_reason_raw="r",
    )
    out = run_agent(
        "AGG74419321 재질 변경", session=session, backend=_Fake([seed_hit]),
        bom_repo=EdgeBomRepository(session), llm=_FakeLlm(),
    )
    assert out.intent.change_attribute == "재질"
    by = {v.part_no: v for v in out.verdicts}
    assert by["REL11110000"].action == "CHECK"  # 사출 ∈ 재질 관련군
    assert by["UNR22220000"].action == "KEEP"   # 전장 ∉ 재질 관련군 → cascade 차단


def test_e2e_no_seeds_graceful(session):
    """retrieval이 비면(임베딩 없음 모사) seed 0개라도 크래시 없이 빈 문서."""
    out = run_agent(
        "존재하지 않는 변경", session=session, backend=_Fake([]),
        bom_repo=EdgeBomRepository(session),
    )
    assert out.orchestration.seeds == []
    assert out.doc.changed_parts == []
    assert validate_doc(out.doc) == []
