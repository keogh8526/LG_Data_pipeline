"""Tests for the Step 9-10 RAG layer interface skeleton.

These verify the interfaces are importable and honestly signal that the
implementation is deferred (DECISIONS D-003).
"""

from __future__ import annotations

import pytest

from src.agent.agent import TOOLS, BomAgent
from src.agent.schema_guided import load_output_schema
from src.og_rag.hypergraph import HyperGraph, build_hypergraph
from src.og_rag.retriever import OGRagRetriever


def test_hypergraph_dataclass_usable() -> None:
    graph = HyperGraph()
    assert graph.edges == {}


def test_build_hypergraph_is_deferred() -> None:
    with pytest.raises(NotImplementedError, match="D-003"):
        build_hypergraph(neo4j_driver=None, qdrant_client=None)


def test_retriever_is_deferred() -> None:
    retriever = OGRagRetriever(HyperGraph())
    with pytest.raises(NotImplementedError, match="D-003"):
        retriever.retrieve("query")


def test_agent_tools_registered() -> None:
    assert set(TOOLS) == {
        "sql_query",
        "graph_traverse",
        "vector_search",
        "axiom_check",
    }


def test_agent_run_is_deferred() -> None:
    with pytest.raises(NotImplementedError, match="D-003"):
        BomAgent().run("query")


def test_output_schema_loads() -> None:
    schema = load_output_schema()
    assert schema["title"] == "ChangeEvent"
