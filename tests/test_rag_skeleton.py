"""Tests for the OG-RAG skeleton and schema-guided output.

OG-RAG is a v2 item (DECISIONS D-003); these verify the interfaces import and
honestly signal deferral.
"""

from __future__ import annotations

import pytest

from src.agent.schema_guided import load_output_schema, validate_against_schema
from src.og_rag.hypergraph import HyperGraph, build_hypergraph
from src.og_rag.retriever import OGRagRetriever


def test_hypergraph_dataclass_usable() -> None:
    graph = HyperGraph()
    assert graph.edges == {}


def test_build_hypergraph_is_deferred() -> None:
    with pytest.raises(NotImplementedError, match="D-003"):
        build_hypergraph(neo4j_driver=None)


def test_retriever_is_deferred() -> None:
    retriever = OGRagRetriever(HyperGraph())
    with pytest.raises(NotImplementedError, match="D-003"):
        retriever.retrieve("query")


def test_output_schema_loads() -> None:
    schema = load_output_schema()
    assert schema["title"] == "ChangeEvent"


def test_validate_against_schema_accepts_valid_payload() -> None:
    event = validate_against_schema(
        {
            "new_part_no": "AB1234568",
            "change_type": "Change",
            "model_code": "WSED7667M.ABMQEUR",
        }
    )
    assert event.new_part_no == "AB1234568"
