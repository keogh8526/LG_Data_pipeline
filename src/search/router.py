"""v2.0 §7-2 — Query Router.

쿼리 텍스트 보고 어느 벡터·SQL을 쓸지 결정. 룰 기반(``config/query_router.yaml``)
이라 결정론적·튜닝 가능. LLM 호출 0회.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml

from src.utils.paths import QUERY_ROUTER_PATH


@dataclass
class SearchPlan:
    """Router 출력 — 실행 계획."""

    mode: str  # 'exact_sql' | 'exact_sql_plus_vector' | 'multi_vector' | ...
    primary_vector: str = "narrative_emb"
    vector_weights: dict[str, float] = field(default_factory=dict)
    sql_filter_field: str | None = None
    sql_filter_values: list[str] = field(default_factory=list)
    join_tables: list[str] = field(default_factory=list)
    expand_graph: bool = True
    case_name: str = "default"


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    return yaml.safe_load(QUERY_ROUTER_PATH.read_text(encoding="utf-8"))


def _findall(pattern: str, query: str, min_len: int = 0) -> list[str]:
    """전체 매치 문자열 추출.

    ``re.findall``은 정규식에 capture group이 있으면 group만 반환 (전체 매치 누락).
    예: ``re.findall(r'\\b[A-Z][A-Z0-9]+(\\.[A-Z0-9]+)?\\b', "WSED7667M.ABMQEUR")``
    → ``['.ABMQEUR']`` (group 1만, 전체 매치 누락).
    ``re.finditer + m.group(0)``으로 전체 매치 보장.
    """
    out: list[str] = []
    for m in re.finditer(pattern, query):
        whole = m.group(0)
        if whole and len(whole) >= min_len:
            out.append(whole)
    return out


def route_query(query: str) -> SearchPlan:
    """쿼리 → SearchPlan. 첫 매칭 case 채택."""
    cfg = _config()
    cases = cfg.get("cases", [])

    for case in cases:
        name = case.get("name", "unknown")
        # pattern 매칭
        pattern = case.get("pattern")
        if pattern:
            min_len = int(case.get("pattern_min_match_len", 0))
            matches = _findall(pattern, query, min_len)
            if matches:
                return SearchPlan(
                    mode=case["mode"],
                    primary_vector=case.get("primary_vector", "narrative_emb"),
                    vector_weights=dict(case.get("vector_weights", {})),
                    sql_filter_field=case.get("sql_filter_field"),
                    sql_filter_values=matches,
                    join_tables=list(case.get("join_tables", [])),
                    expand_graph=bool(case.get("expand_graph", True)),
                    case_name=name,
                )

        # keyword 매칭
        keywords = case.get("keywords")
        if keywords and any(kw in query for kw in keywords):
            return SearchPlan(
                mode=case["mode"],
                primary_vector=case.get("primary_vector", "narrative_emb"),
                vector_weights=dict(case.get("vector_weights", {})),
                join_tables=list(case.get("join_tables", [])),
                expand_graph=bool(case.get("expand_graph", True)),
                case_name=name,
            )

    # 기본 plan
    default = cfg.get("default", {})
    return SearchPlan(
        mode=default.get("mode", "single_vector"),
        primary_vector=default.get("primary_vector", "narrative_emb"),
        expand_graph=bool(default.get("expand_graph", True)),
        case_name="default",
    )


def retrieval_params() -> dict[str, int]:
    """retrieve 섹션 파라미터 반환."""
    cfg = _config()
    return dict(cfg.get("retrieve", {}))
