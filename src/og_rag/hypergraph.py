"""Step 9 — OG-RAG hypergraph construction (interface skeleton).

Builds an ontology-grounded hypergraph from the v1.2 ontology and the Neo4j
graph (graph + native vector index). Each hyperedge represents one ontology
entity instance together with its properties and relations.

This module is an interface skeleton. The construction logic depends on real
data and is deferred — see DECISIONS D-003. Reference structure:
microsoft/ograg2 and OG-RAG (Sharma et al., EMNLP 2025).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class HyperEdge:
    """One ontology-entity instance and its grounded context.

    Attributes:
        edge_id: Stable hyperedge identifier.
        entity_type: Ontology entity type (e.g. ``"ChangeEvent"``).
        node_ids: Member node ids spanned by this hyperedge.
        properties: Flattened ontology properties of the instance.
    """

    edge_id: str
    entity_type: str
    node_ids: list[str] = field(default_factory=list)
    properties: dict[str, object] = field(default_factory=dict)


@dataclass
class HyperGraph:
    """An ontology-grounded hypergraph.

    Attributes:
        edges: All hyperedges, keyed by ``edge_id``.
    """

    edges: dict[str, HyperEdge] = field(default_factory=dict)


def build_hypergraph(
    neo4j_driver: object,
    max_cardinality: int = 8,
) -> HyperGraph:
    """Construct the OG-RAG hypergraph from the Neo4j graph + vector index.

    Args:
        neo4j_driver: A Neo4j driver providing the property graph and the
            native vector index.
        max_cardinality: Upper bound on member nodes per hyperedge.

    Returns:
        The constructed :class:`HyperGraph`.

    Raises:
        NotImplementedError: Always — implementation deferred (DECISIONS D-003).
    """
    # TODO(real-data): build hyperedges per ontology entity instance, then run
    # optimization-based hyperedge selection under the cardinality constraint.
    raise NotImplementedError("OG-RAG hypergraph construction deferred (D-003).")
