"""Step 10 — BOM agent (interface skeleton).

The agent classifies query intent, selects a tool, synthesizes results, and
emits schema-constrained output. Tool implementations and the orchestration
loop are deferred until real data and an LLM API key are available — see
DECISIONS D-003.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

# Tool registry. Each tool is a callable; bodies are deferred.
ToolFn = Callable[[str], object]


def sql_query(query: str) -> object:
    """Run a structured query against PostgreSQL.

    Args:
        query: A natural-language or structured query.

    Raises:
        NotImplementedError: Always — deferred (D-003).
    """
    raise NotImplementedError("sql_query tool deferred (D-003).")


def graph_traverse(query: str) -> object:
    """Traverse the Neo4j graph for a query.

    Args:
        query: A traversal request.

    Raises:
        NotImplementedError: Always — deferred (D-003).
    """
    raise NotImplementedError("graph_traverse tool deferred (D-003).")


def vector_search(query: str) -> object:
    """Run hybrid vector search for a query.

    Args:
        query: A free-text query.

    Raises:
        NotImplementedError: Always — deferred (D-003).
    """
    raise NotImplementedError("vector_search tool deferred (D-003).")


def axiom_check(query: str) -> object:
    """Validate a candidate result against deterministic axioms.

    Args:
        query: The value(s) to check.

    Raises:
        NotImplementedError: Always — deferred (D-003).
    """
    raise NotImplementedError("axiom_check tool deferred (D-003).")


TOOLS: dict[str, ToolFn] = {
    "sql_query": sql_query,
    "graph_traverse": graph_traverse,
    "vector_search": vector_search,
    "axiom_check": axiom_check,
}


@dataclass
class AgentResult:
    """The outcome of an agent run.

    Attributes:
        answer: The synthesized, schema-conformant answer.
        tools_used: Names of tools invoked, in order.
        needs_review: Fields flagged as low-confidence for human review.
    """

    answer: dict[str, object] = field(default_factory=dict)
    tools_used: list[str] = field(default_factory=list)
    needs_review: list[str] = field(default_factory=list)


class BomAgent:
    """Orchestrates intent classification, tool use, and schema-guided output."""

    MAX_RETRIES = 3

    def run(self, query: str) -> AgentResult:
        """Answer a query end-to-end.

        Args:
            query: The user query.

        Returns:
            An :class:`AgentResult`.

        Raises:
            NotImplementedError: Always — orchestration deferred (D-003).
        """
        # TODO(real-data): classify intent -> select tool(s) -> synthesize ->
        # emit schema-guided output, retrying on axiom violations (<= MAX_RETRIES).
        raise NotImplementedError("Agent orchestration deferred (D-003).")
