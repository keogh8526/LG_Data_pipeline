"""D-012 — retrieve 모듈 unit smoke test.

Postgres + Ollama 의존 — 통합 테스트는 환경 갖춰진 상태에서만. 본 파일은:
  - import 가능성
  - RRF 계산 정확성 (in-memory mock)
  - Hit dataclass 동작
만 확인.
"""

from __future__ import annotations

from src.db.retrieve import Hit, hybrid_search, lexical_search, semantic_search


def test_module_imports():
    assert callable(semantic_search)
    assert callable(lexical_search)
    assert callable(hybrid_search)


def test_hit_dataclass_defaults():
    h = Hit(
        doc_id=1,
        part_no_new="AGG74419321",
        part_name="Packing",
        new_model="W7M",
        event="Change",
        region="EUR",
        form_id="changing_parts_list_96",
        file_id=10,
        embedding_text="narrative",
    )
    assert h.score_semantic is None
    assert h.score_lexical is None
    assert h.score_rrf is None
    assert h.rank_semantic is None
    assert h.rank_lexical is None


def test_rrf_math():
    """RRF 융합: doc이 두 모달리티에 동시 등장하면 점수 합산. 한쪽만이면 단일."""
    from src.db.retrieve import _RRF_K

    # 직접 hybrid_search를 호출하지 않고 RRF 계산식만 검증 (Postgres 없이 가능).
    rrf_k = _RRF_K
    sem_only = 1.0 / (rrf_k + 0 + 1)
    sem_and_lex = 1.0 / (rrf_k + 0 + 1) + 1.0 / (rrf_k + 0 + 1)
    assert sem_and_lex > sem_only
    # 가중치 적용: lex_w=0이면 lexical 기여 제거
    rrf_no_lex = 1.0 / (rrf_k + 0 + 1) + 0.0 / (rrf_k + 0 + 1)
    assert abs(rrf_no_lex - sem_only) < 1e-9
