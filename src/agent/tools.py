"""Step 10 — BOM agent tools.

Four tools back the agent's retrieve/apply nodes. ``apply_change_to_bom`` and
``validate_bom`` are fully deterministic and implemented here. ``local_search``
and ``structured_query`` need a running Neo4j instance; their bodies are
deferred until real data is loaded — see DECISIONS D-003.
"""

from __future__ import annotations

from ontology import axioms


def local_search(driver: object, query: str, top_k: int = 10) -> list[dict[str, object]]:
    """Hybrid vector + 1-hop graph search for free-text queries.

    Args:
        driver: A Neo4j driver.
        query: Free-text query.
        top_k: Number of results.

    Raises:
        NotImplementedError: Body deferred until real data (D-003). Wire this
            to :func:`src.embed.search.hybrid_search` once embeddings exist.
    """
    raise NotImplementedError("local_search deferred (D-003).")


def structured_query(driver: object, cypher: str) -> list[dict[str, object]]:
    """Run a structured Cypher query against Neo4j.

    Args:
        driver: A Neo4j driver.
        cypher: A Cypher query string.

    Returns:
        Result records as dicts.
    """
    with driver.session() as session:  # type: ignore[attr-defined]
        return [dict(record) for record in session.run(cypher)]


def apply_change_to_bom(
    bom: list[dict[str, object]],
    change_events: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Apply change events to a base BOM, deterministically.

    A ``Change`` swaps a part number; ``New`` appends; ``Carry-over`` is a
    no-op. The input BOM is not mutated.

    Args:
        bom: Base BOM rows, each with a ``part_no`` key.
        change_events: Change events with ``change_type``, ``base_part_no``,
            ``new_part_no``.

    Returns:
        The resulting BOM rows.
    """
    result = [dict(row) for row in bom]
    index = {row.get("part_no"): row for row in result}
    for event in change_events:
        change_type = event.get("change_type")
        base = event.get("base_part_no")
        new = event.get("new_part_no")
        if change_type == "Change" and base in index:
            index[base]["part_no"] = new
        elif change_type == "New" and new not in index:
            row = {"part_no": new, "part_name": event.get("part_name")}
            result.append(row)
            index[new] = row
    return result


def validate_bom(bom: list[dict[str, object]]) -> dict[str, object]:
    """Validate a BOM against deterministic axioms.

    Args:
        bom: BOM rows, each with a ``part_no`` key.

    Returns:
        A report with ``invalid_part_nos``, ``duplicate_part_nos``, and an
        overall ``passed`` flag.
    """
    seen: set[str] = set()
    invalid: list[str] = []
    duplicates: list[str] = []
    for row in bom:
        part_no = str(row.get("part_no", ""))
        if not axioms.validate_part_no(part_no):
            invalid.append(part_no)
        if part_no in seen:
            duplicates.append(part_no)
        seen.add(part_no)
    return {
        "invalid_part_nos": invalid,
        "duplicate_part_nos": duplicates,
        "passed": not invalid and not duplicates,
    }


TOOLS = {
    "local_search": local_search,
    "structured_query": structured_query,
    "apply_change_to_bom": apply_change_to_bom,
    "validate_bom": validate_bom,
}
