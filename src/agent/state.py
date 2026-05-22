"""Step 10 — LangGraph agent state.

``AgentState`` is the shared state threaded through every node of the BOM
agent graph. It accumulates parsed input, retrieval results, drafts, the
validation report, and an audit log.
"""

from __future__ import annotations

from typing import TypedDict

MAX_RETRIES = 3


class AgentState(TypedDict, total=False):
    """State passed between BOM-agent nodes.

    Keys are populated progressively as the graph runs; ``total=False`` lets
    early nodes write only the fields they own.
    """

    # Input.
    change_point_raw: str
    parsed_change_point: dict[str, object]

    # Retrieval / base-model selection.
    candidate_models: list[dict[str, object]]
    selected_base_model: str
    reasoning: str

    # Drafts and validation.
    bom_draft: list[dict[str, object]]
    master_draft: dict[str, object]
    validation_report: dict[str, object]

    # Control / provenance.
    audit_log: list[str]
    retry_count: int
    needs_human_review: bool
