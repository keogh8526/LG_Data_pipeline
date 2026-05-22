"""Tests for the Step 10 LangGraph BOM agent.

Deterministic nodes/tools are tested directly; LLM-backed nodes are verified to
fail loudly as deferred (DECISIONS D-003). The graph is checked to compile.
"""

from __future__ import annotations

import pytest

from src.agent.graph import build_agent_graph
from src.agent.nodes import (
    apply_bom_diff,
    parse_change_point,
    route_after_validation,
    validate_rules,
)
from src.agent.state import AgentState
from src.agent.tools import TOOLS, apply_change_to_bom, validate_bom


def test_tools_registered() -> None:
    assert set(TOOLS) == {
        "local_search",
        "structured_query",
        "apply_change_to_bom",
        "validate_bom",
    }


def test_apply_change_to_bom_swaps_and_appends() -> None:
    bom = [{"part_no": "AB1234567"}]
    events = [
        {"change_type": "Change", "base_part_no": "AB1234567", "new_part_no": "AB1234568"},
        {"change_type": "New", "new_part_no": "CD7654321"},
    ]
    result = apply_change_to_bom(bom, events)
    part_nos = {row["part_no"] for row in result}
    assert part_nos == {"AB1234568", "CD7654321"}
    # Input not mutated.
    assert bom[0]["part_no"] == "AB1234567"


def test_validate_bom_flags_invalid_and_duplicates() -> None:
    report = validate_bom([{"part_no": "AB1234567"}, {"part_no": "123"}])
    assert report["invalid_part_nos"] == ["123"]
    assert not report["passed"]


def test_apply_bom_diff_node() -> None:
    state = AgentState(
        bom_draft=[{"part_no": "AB1234567"}],
        parsed_change_point={
            "change_events": [
                {
                    "change_type": "Change",
                    "base_part_no": "AB1234567",
                    "new_part_no": "AB1234568",
                }
            ]
        },
    )
    update = apply_bom_diff(state)
    assert update["bom_draft"][0]["part_no"] == "AB1234568"


def test_validate_rules_node_increments_retry() -> None:
    update = validate_rules(AgentState(bom_draft=[{"part_no": "123"}]))
    assert update["retry_count"] == 1
    assert not update["validation_report"]["passed"]


def test_route_after_validation() -> None:
    failed = AgentState(validation_report={"passed": False}, retry_count=1)
    assert route_after_validation(failed) == "retry"
    passed = AgentState(validation_report={"passed": True}, retry_count=1)
    assert route_after_validation(passed) == "end"
    exhausted = AgentState(validation_report={"passed": False}, retry_count=3)
    assert route_after_validation(exhausted) == "end"


def test_llm_node_is_deferred() -> None:
    with pytest.raises(NotImplementedError, match="D-003"):
        parse_change_point(AgentState(change_point_raw="x"))


def test_agent_graph_compiles() -> None:
    app = build_agent_graph()
    assert app is not None
