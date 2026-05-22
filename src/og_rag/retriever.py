"""Step 9 — OG-RAG retriever (interface skeleton).

Retrieves grounded context from the hypergraph for a query. Implementation is
deferred until real data is available — see DECISIONS D-003. The signature is
compatible with ``src.eval.runner.evaluate_retrieval`` so it can be injected as
the retrieval callable once implemented.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.og_rag.hypergraph import HyperGraph


@dataclass
class RetrievedContext:
    """Hypergraph context retrieved for a query.

    Attributes:
        event_ids: Ranked change-event ids, best-first.
        hyperedge_ids: Hyperedges contributing the context.
        passages: Free-text passages for downstream generation.
    """

    event_ids: list[str] = field(default_factory=list)
    hyperedge_ids: list[str] = field(default_factory=list)
    passages: list[str] = field(default_factory=list)


class OGRagRetriever:
    """Ontology-grounded retriever over an OG-RAG hypergraph."""

    def __init__(self, hypergraph: HyperGraph) -> None:
        """Initialize the retriever.

        Args:
            hypergraph: The hypergraph to retrieve from.
        """
        self.hypergraph = hypergraph

    def retrieve(self, query: str, top_k: int = 10) -> RetrievedContext:
        """Retrieve grounded context for a query.

        Args:
            query: Natural-language query.
            top_k: Number of change events to return.

        Returns:
            The retrieved context.

        Raises:
            NotImplementedError: Always — implementation deferred (D-003).
        """
        # TODO(real-data): score hyperedges against the query, expand by
        # ontology relations, and rank member change events.
        raise NotImplementedError("OG-RAG retrieval deferred (D-003).")
