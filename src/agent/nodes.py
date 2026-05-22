"""Step 10 — BOM agent node functions.

Each node is a pure function ``AgentState -> AgentState`` (returning the keys it
updates). Deterministic nodes (``apply_bom_diff``, ``validate_rules``) are fully
implemented; LLM-backed nodes are deferred until a local LLM is wired in — see
DECISIONS D-003.
"""

from __future__ import annotations

from src.agent.state import MAX_RETRIES, AgentState
from src.agent.tools import apply_change_to_bom, validate_bom
from src.utils.logging import get_logger

log = get_logger(__name__)


def _log(state: AgentState, message: str) -> list[str]:
    """Return the audit log extended with ``message``."""
    return [*state.get("audit_log", []), message]


def parse_change_point(state: AgentState) -> AgentState:
    """Node 1 — parse raw change-point text into structured fields (LLM).

    Args:
        state: Current agent state.

    Raises:
        NotImplementedError: Deferred — requires the local LLM with
            schema-guided output (D-003).
    """
    # TODO(real-data): call the local LLM via src.agent.schema_guided.
    raise NotImplementedError("parse_change_point deferred (D-003).")


def retrieve(state: AgentState) -> AgentState:
    """Node 2 — select a tool and retrieve candidate base models.

    Args:
        state: Current agent state.

    Raises:
        NotImplementedError: Deferred — requires Neo4j data and tool wiring
            (D-003).
    """
    # TODO(real-data): pick local_search vs structured_query, then run it.
    raise NotImplementedError("retrieve deferred (D-003).")


def select_base_model(state: AgentState) -> AgentState:
    """Node 3 — choose the base model from retrieved candidates (LLM).

    Args:
        state: Current agent state.

    Raises:
        NotImplementedError: Deferred — requires the local LLM (D-003).
    """
    # TODO(real-data): call the local LLM with schema-guided output.
    raise NotImplementedError("select_base_model deferred (D-003).")


def apply_bom_diff(state: AgentState) -> AgentState:
    """Node 4 — apply parsed changes to the base BOM (deterministic).

    Args:
        state: Current agent state. Reads ``bom_draft`` (base BOM) and
            ``parsed_change_point['change_events']``.

    Returns:
        State updates with the new ``bom_draft`` and an audit-log entry.
    """
    base_bom = state.get("bom_draft", [])
    change_events = state.get("parsed_change_point", {}).get("change_events", [])
    new_bom = apply_change_to_bom(base_bom, list(change_events))  # type: ignore[arg-type]
    log.info("agent.apply_bom_diff", rows=len(new_bom))
    return AgentState(
        bom_draft=new_bom,
        audit_log=_log(state, f"apply_bom_diff: {len(new_bom)} rows"),
    )


def validate_rules(state: AgentState) -> AgentState:
    """Node 5 — validate the BOM draft against axioms (deterministic).

    Args:
        state: Current agent state. Reads ``bom_draft``.

    Returns:
        State updates with ``validation_report``, an incremented
        ``retry_count``, and the ``needs_human_review`` flag.
    """
    report = validate_bom(state.get("bom_draft", []))
    retry_count = state.get("retry_count", 0) + 1
    needs_review = not report["passed"] and retry_count >= MAX_RETRIES
    log.info("agent.validate_rules", passed=report["passed"], retry=retry_count)
    return AgentState(
        validation_report=report,
        retry_count=retry_count,
        needs_human_review=needs_review,
        audit_log=_log(state, f"validate_rules: passed={report['passed']}"),
    )


def route_after_validation(state: AgentState) -> str:
    """Conditional edge — retry on failure, else end.

    Args:
        state: Current agent state.

    Returns:
        ``"retry"`` to loop back to base-model selection, or ``"end"``.
    """
    report = state.get("validation_report", {})
    retry_count = state.get("retry_count", 0)
    if not report.get("passed", False) and retry_count < MAX_RETRIES:
        return "retry"
    return "end"
