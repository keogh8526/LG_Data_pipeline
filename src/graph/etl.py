"""Step 6 — PostgreSQL -> Neo4j ETL (deterministic, no LLM).

PostgreSQL is the single source of truth; the Neo4j graph is a derived view.
Node/edge batches are built as plain dicts so the build logic is unit-testable
without a running database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd

from src.utils.logging import get_logger


def _present(value: object) -> bool:
    """Return True if a value is a non-empty, non-NaN field."""
    return value is not None and not (isinstance(value, float) and pd.isna(value)) and value != ""

log = get_logger(__name__)

# Schema constraints and indexes, applied once before loading.
CONSTRAINTS: tuple[str, ...] = (
    "CREATE CONSTRAINT part_no IF NOT EXISTS "
    "FOR (p:Part) REQUIRE p.part_no IS UNIQUE",
    "CREATE CONSTRAINT model_code IF NOT EXISTS "
    "FOR (m:Model) REQUIRE m.model_code IS UNIQUE",
    "CREATE INDEX event_form_version IF NOT EXISTS "
    "FOR (e:ChangeEvent) ON (e.form_version)",
)

# Validation Cypher queries — each should return 0 (or only New events).
VALIDATION_QUERIES: dict[str, str] = {
    "parts_without_model": (
        "MATCH (p:Part) WHERE NOT (p)-[:BELONGS_TO]->(:Model) RETURN count(p)"
    ),
    "events_missing_side": (
        "MATCH (e:ChangeEvent) "
        "WHERE NOT (e)-[:CHANGED_FROM]->() OR NOT (e)-[:CHANGED_TO]->() "
        "RETURN count(e)"
    ),
}


@dataclass
class GraphBatch:
    """Node and edge parameter batches for a single UNWIND load."""

    parts: list[dict[str, object]]
    models: list[dict[str, object]]
    change_events: list[dict[str, object]]
    belongs_to: list[dict[str, object]]
    changed_from: list[dict[str, object]]
    changed_to: list[dict[str, object]]


def build_batch(
    parts: pd.DataFrame,
    models: pd.DataFrame,
    change_events: pd.DataFrame,
) -> GraphBatch:
    """Build node/edge parameter batches from relational tables.

    Args:
        parts: Parts table.
        models: Models table.
        change_events: Change-events table.

    Returns:
        A :class:`GraphBatch` ready to feed UNWIND statements.
    """
    part_rows = parts.to_dict(orient="records")
    model_rows = models.to_dict(orient="records")
    event_rows = change_events.to_dict(orient="records")

    belongs_to: list[dict[str, object]] = []
    changed_from: list[dict[str, object]] = []
    changed_to: list[dict[str, object]] = []
    for event in event_rows:
        if _present(event.get("model_code")) and _present(event.get("new_part_no")):
            belongs_to.append(
                {"part_no": event["new_part_no"], "model_code": event["model_code"]}
            )
        if _present(event.get("base_part_no")):
            changed_from.append(
                {"event_id": event["event_id"], "part_no": event["base_part_no"]}
            )
        if _present(event.get("new_part_no")):
            changed_to.append(
                {"event_id": event["event_id"], "part_no": event["new_part_no"]}
            )

    log.info(
        "graph.build_batch",
        parts=len(part_rows),
        models=len(model_rows),
        events=len(event_rows),
    )
    return GraphBatch(
        parts=part_rows,
        models=model_rows,
        change_events=event_rows,
        belongs_to=belongs_to,
        changed_from=changed_from,
        changed_to=changed_to,
    )


# UNWIND load statements, parameterized by ``$rows``.
LOAD_PARTS = (
    "UNWIND $rows AS row MERGE (p:Part {part_no: row.part_no}) "
    "SET p.part_name = row.part_name, p.bom_level = row.bom_level, "
    "p.part_type = row.part_type"
)
LOAD_MODELS = (
    "UNWIND $rows AS row MERGE (m:Model {model_code: row.model_code}) "
    "SET m.grade = row.grade, m.region = row.region"
)
LOAD_EVENTS = (
    "UNWIND $rows AS row MERGE (e:ChangeEvent {event_id: row.event_id}) "
    "SET e.change_type = row.change_type, e.form_version = row.form_version, "
    "e.change_point = row.change_point"
)
LOAD_BELONGS_TO = (
    "UNWIND $rows AS row MATCH (p:Part {part_no: row.part_no}), "
    "(m:Model {model_code: row.model_code}) MERGE (p)-[:BELONGS_TO]->(m)"
)
LOAD_CHANGED_FROM = (
    "UNWIND $rows AS row MATCH (e:ChangeEvent {event_id: row.event_id}), "
    "(p:Part {part_no: row.part_no}) MERGE (e)-[:CHANGED_FROM]->(p)"
)
LOAD_CHANGED_TO = (
    "UNWIND $rows AS row MATCH (e:ChangeEvent {event_id: row.event_id}), "
    "(p:Part {part_no: row.part_no}) MERGE (e)-[:CHANGED_TO]->(p)"
)


def make_driver() -> object:
    """Create a Neo4j driver from environment variables.

    Returns:
        A ``neo4j.Driver`` instance.
    """
    from neo4j import GraphDatabase  # local import — optional at module load

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "neo4j_password")
    return GraphDatabase.driver(uri, auth=(user, password))


def load_graph(driver: object, batch: GraphBatch) -> None:
    """Apply constraints and load a batch into Neo4j.

    Args:
        driver: A Neo4j driver.
        batch: The batch to load.
    """
    with driver.session() as session:  # type: ignore[attr-defined]
        for constraint in CONSTRAINTS:
            session.run(constraint)
        session.run(LOAD_PARTS, rows=batch.parts)
        session.run(LOAD_MODELS, rows=batch.models)
        session.run(LOAD_EVENTS, rows=batch.change_events)
        session.run(LOAD_BELONGS_TO, rows=batch.belongs_to)
        session.run(LOAD_CHANGED_FROM, rows=batch.changed_from)
        session.run(LOAD_CHANGED_TO, rows=batch.changed_to)
    log.info("graph.loaded", parts=len(batch.parts), events=len(batch.change_events))
