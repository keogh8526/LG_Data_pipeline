"""Step 10 — BOM agent LangGraph wiring.

Builds the 5-node state graph:

    parse_change_point -> retrieve -> select_base_model
        -> apply_bom_diff -> validate_rules
    validate_rules --[fail & retry<3]--> select_base_model
    validate_rules --[pass | retry>=3]--> END
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from src.agent import nodes
from src.agent.state import AgentState


def build_agent_graph() -> object:
    """Build and compile the BOM-agent state graph.

    Returns:
        A compiled LangGraph application.
    """
    graph: StateGraph = StateGraph(AgentState)

    graph.add_node("parse_change_point", nodes.parse_change_point)
    graph.add_node("retrieve", nodes.retrieve)
    graph.add_node("select_base_model", nodes.select_base_model)
    graph.add_node("apply_bom_diff", nodes.apply_bom_diff)
    graph.add_node("validate_rules", nodes.validate_rules)

    graph.set_entry_point("parse_change_point")
    graph.add_edge("parse_change_point", "retrieve")
    graph.add_edge("retrieve", "select_base_model")
    graph.add_edge("select_base_model", "apply_bom_diff")
    graph.add_edge("apply_bom_diff", "validate_rules")
    graph.add_conditional_edges(
        "validate_rules",
        nodes.route_after_validation,
        {"retry": "select_base_model", "end": END},
    )
    return graph.compile()
