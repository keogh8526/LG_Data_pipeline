"""Step 6 — processed data -> Neo4j ETL (deterministic, no LLM).

Neo4j is the single store for the MVP: it holds the property graph *and* the
text vector indexes. Node/edge batches are built as plain dicts so the build
logic is unit-testable without a running database.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pandas as pd
import typer

from src.utils.logging import get_logger

log = get_logger(__name__)


def _present(value: object) -> bool:
    """Return True if a value is a non-empty, non-NaN field."""
    return (
        value is not None
        and not (isinstance(value, float) and pd.isna(value))
        and value != ""
    )


# Schema constraints and indexes, applied once before loading.
CONSTRAINTS: tuple[str, ...] = (
    "CREATE CONSTRAINT part_no IF NOT EXISTS "
    "FOR (p:Part) REQUIRE p.part_no IS UNIQUE",
    "CREATE CONSTRAINT model_code IF NOT EXISTS "
    "FOR (m:Model) REQUIRE m.model_code IS UNIQUE",
    "CREATE CONSTRAINT form_version IF NOT EXISTS "
    "FOR (f:FormVersion) REQUIRE f.version IS UNIQUE",
    "CREATE INDEX event_form_version IF NOT EXISTS "
    "FOR (e:ChangeEvent) ON (e.form_version)",
)

# Native Neo4j vector indexes over free-text embeddings. ``$dim`` is the
# embedding dimensionality (1024 for BGE-M3).
VECTOR_INDEX_TEMPLATE = (
    "CREATE VECTOR INDEX {name} IF NOT EXISTS "
    "FOR (e:ChangeEvent) ON (e.{field}_embedding) "
    "OPTIONS {{indexConfig: {{`vector.dimensions`: $dim, "
    "`vector.similarity_function`: 'cosine'}}}}"
)
VECTOR_INDEXED_FIELDS: tuple[str, ...] = ("change_point", "change_reason")

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
    form_versions: list[dict[str, object]] = field(default_factory=list)
    uses_form: list[dict[str, object]] = field(default_factory=list)


def build_batch(
    parts: pd.DataFrame,
    models: pd.DataFrame,
    change_events: pd.DataFrame,
    form_versions: pd.DataFrame | None = None,
) -> GraphBatch:
    """Build node/edge parameter batches from processed tables.

    Args:
        parts: Parts table.
        models: Models table.
        change_events: Change-events table.
        form_versions: Optional form-version history table.

    Returns:
        A :class:`GraphBatch` ready to feed UNWIND statements.
    """
    part_rows = parts.to_dict(orient="records")
    model_rows = models.to_dict(orient="records")
    event_rows = change_events.to_dict(orient="records")
    form_rows = (
        form_versions.to_dict(orient="records") if form_versions is not None else []
    )

    belongs_to: list[dict[str, object]] = []
    changed_from: list[dict[str, object]] = []
    changed_to: list[dict[str, object]] = []
    uses_form: list[dict[str, object]] = []
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
        if _present(event.get("form_version")):
            uses_form.append(
                {"event_id": event["event_id"], "version": event["form_version"]}
            )

    log.info(
        "graph.build_batch",
        parts=len(part_rows),
        models=len(model_rows),
        events=len(event_rows),
        form_versions=len(form_rows),
    )
    return GraphBatch(
        parts=part_rows,
        models=model_rows,
        change_events=event_rows,
        belongs_to=belongs_to,
        changed_from=changed_from,
        changed_to=changed_to,
        form_versions=form_rows,
        uses_form=uses_form,
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
    "e.change_point = row.change_point, e.change_reason = row.change_reason, "
    "e.source_file = row.source_file"
)
LOAD_FORM_VERSIONS = (
    "UNWIND $rows AS row MERGE (f:FormVersion {version: row.version}) "
    "SET f.released_at = row.released_at, f.change_summary = row.change_summary"
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
LOAD_USES_FORM = (
    "UNWIND $rows AS row MATCH (e:ChangeEvent {event_id: row.event_id}), "
    "(f:FormVersion {version: row.version}) MERGE (e)-[:USES_FORM]->(f)"
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


def init_schema(driver: object, embedding_dim: int = 1024) -> None:
    """Apply constraints and create native vector indexes.

    Args:
        driver: A Neo4j driver.
        embedding_dim: Embedding dimensionality for the vector indexes.
    """
    with driver.session() as session:  # type: ignore[attr-defined]
        for constraint in CONSTRAINTS:
            session.run(constraint)
        for field_name in VECTOR_INDEXED_FIELDS:
            session.run(
                VECTOR_INDEX_TEMPLATE.format(
                    name=f"{field_name}_vec", field=field_name
                ),
                dim=embedding_dim,
            )
    log.info("graph.init_schema", embedding_dim=embedding_dim)


def load_graph(driver: object, batch: GraphBatch) -> None:
    """Load a batch into Neo4j (schema must already exist).

    Args:
        driver: A Neo4j driver.
        batch: The batch to load.
    """
    with driver.session() as session:  # type: ignore[attr-defined]
        session.run(LOAD_PARTS, rows=batch.parts)
        session.run(LOAD_MODELS, rows=batch.models)
        session.run(LOAD_EVENTS, rows=batch.change_events)
        session.run(LOAD_FORM_VERSIONS, rows=batch.form_versions)
        session.run(LOAD_BELONGS_TO, rows=batch.belongs_to)
        session.run(LOAD_CHANGED_FROM, rows=batch.changed_from)
        session.run(LOAD_CHANGED_TO, rows=batch.changed_to)
        session.run(LOAD_USES_FORM, rows=batch.uses_form)
    log.info("graph.loaded", parts=len(batch.parts), events=len(batch.change_events))


app = typer.Typer(help="Step 6 — Neo4j schema and ETL.")


@app.command("init-schema")
def init_schema_cmd(
    embedding_dim: int = typer.Option(1024, help="Vector index dimensionality."),
) -> None:
    """Create Neo4j constraints and native vector indexes.

    Replaces the removed PostgreSQL ``init-db`` step (DECISIONS D-006). Requires
    a running Neo4j — configure connection via the ``NEO4J_*`` env vars.
    """
    driver = make_driver()
    try:
        init_schema(driver, embedding_dim=embedding_dim)
        typer.echo(f"Neo4j schema initialized (embedding_dim={embedding_dim}).")
    finally:
        driver.close()  # type: ignore[attr-defined]


if __name__ == "__main__":
    app()
