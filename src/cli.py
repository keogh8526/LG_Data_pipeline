"""Unified Typer CLI for the LG BOM preprocessing pipeline.

Run with ``python -m src.cli <command>``. Step 0-5 commands:

    inventory                      scan raw Excel files into a parquet inventory
    classify                       classify the form version of files
    schema-export                  export the answer schema as JSON Schema
    preprocess                     one-shot preprocessing preview (no persistence)

    pipeline run <path>            full run: process + validate + diff + report
    pipeline review --run-id ID    open the report / state for a run
    pipeline commit --run-id ID    promote a dry-run to committed
    pipeline rollback --run-id ID  move a committed run to rolled_back

    quarantine list --run-id ID    list quarantined rows for a run

    db init                        create tables + (Postgres) pgvector / pg_trgm
    db load --run-id ID            load a committed run into Postgres
    db rollback --run-id ID        delete a run's change events from Postgres
    db status                      list runs in preprocessing_runs
    db verify --run-id ID          per-table row counts for a run
    search QUERY                   hybrid pgvector + pg_trgm search
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from sqlalchemy import func, select

from src.db.engine import init_db, make_engine, session_factory
from src.db.load import load_run, update_embeddings
from src.db.models import ChangeEvent, Model, Part, PreprocessingRun
from src.db.rollback import rollback_run as db_rollback_run
from src.preprocess.classify import classify_dir, classify_form
from src.preprocess.inventory import build_inventory
from src.preprocess.pipeline import (
    COMMITTED_ROOT,
    commit_run,
    discover_raw_files,
    generate_run_id,
    preprocess_directory,
    preprocess_file,
    read_state,
    reprocess_quarantine,
    rollback_run,
    run_pipeline,
)
from src.preprocess.quarantine import list_quarantined
from src.utils.paths import INTERIM_DIR, RAW_DIR, SCHEMA_JSON_PATH

app = typer.Typer(help="LG BOM 전처리 파이프라인 CLI.")
pipeline_app = typer.Typer(help="dry-run / commit / rollback 사이클.")
quarantine_app = typer.Typer(help="격리된 행 조회.")
db_app = typer.Typer(help="PostgreSQL 적재 / 롤백 / 상태.")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(quarantine_app, name="quarantine")
app.add_typer(db_app, name="db")


# --- Top-level commands ---------------------------------------------------


@app.command()
def inventory(
    raw_dir: Path = typer.Option(RAW_DIR, help="Directory of raw Excel files."),
    output: Path = typer.Option(
        INTERIM_DIR / "file_inventory.parquet", help="Output parquet path."
    ),
) -> None:
    """Step 0 — scan raw files and write the inventory parquet."""
    df = build_inventory(raw_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, index=False)

    if df.empty:
        typer.echo(f"No Excel files found under {raw_dir}.")
        return

    typer.echo(f"Inventory written to {output} ({len(df)} sheet rows).")
    typer.echo("\nForm-version guess (per file):")
    per_file = df.drop_duplicates("file_path")["form_version_guess"]
    for version, count in per_file.value_counts().items():
        typer.echo(f"  {version}: {count}")
    typer.echo("\nHead:")
    typer.echo(df.head(20).to_string())


@app.command()
def classify(
    path: Path = typer.Argument(..., help="Excel file, or directory with --all."),
    all_files: bool = typer.Option(
        False, "--all", help="Treat PATH as a directory and classify every file."
    ),
) -> None:
    """Step 1 — classify the form version of one file or a directory."""
    if all_files or path.is_dir():
        results = classify_dir(path)
        counts: dict[str, int] = {}
        for result in results:
            counts[result.form_version] = counts.get(result.form_version, 0) + 1
            flag = " [needs review]" if result.needs_review else ""
            typer.echo(
                f"{Path(result.file_path).name}: {result.form_version} "
                f"({result.confidence}){flag}"
            )
        typer.echo("\nForm-version counts:")
        for version, count in sorted(counts.items()):
            typer.echo(f"  {version}: {count}")
        return

    result = classify_form(path)
    typer.echo(f"{path.name}: {result.form_version} (confidence={result.confidence})")
    typer.echo(f"  needs_review: {result.needs_review}")
    typer.echo("  scores:")
    for version, score in sorted(result.evidence.items()):
        typer.echo(f"    {version}: {score}")


@app.command("schema-export")
def schema_export(
    output: Path = typer.Option(SCHEMA_JSON_PATH, help="Output JSON Schema path."),
) -> None:
    """Step 2 — export the ChangeEventRow answer schema as JSON Schema."""
    from src.ontology.schema import export_schema_json

    export_schema_json(output)
    typer.echo(f"Schema exported to {output}.")


@app.command()
def preprocess(
    path: Path = typer.Argument(..., help="Excel file or directory of raw files."),
    run_id: str = typer.Option("", help="Batch id (defaults to a fresh one)."),
) -> None:
    """Step 3 — quick preview without persistence. Use `pipeline run` for the full cycle."""
    run = run_id or generate_run_id()
    if path.is_dir():
        summary = preprocess_directory(path, run)
        typer.echo(f"Run {summary.run_id}: {len(summary.results)} files")
        typer.echo(
            f"  rows_in={summary.rows_in}  rows_out={summary.rows_out}  "
            f"quarantined={summary.quarantine_count}"
        )
        for result in summary.results:
            typer.echo(
                f"  {Path(result.file_path).name}: {result.status} "
                f"[{result.form_version or '-'}]  "
                f"rows={result.rows_out}  q={result.quarantine_count}"
            )
        return

    result = preprocess_file(path, run)
    typer.echo(f"Run {result.run_id}")
    typer.echo(f"  status={result.status}  form={result.form_version}")
    typer.echo(
        f"  rows_in={result.rows_in}  rows_out={result.rows_out}  "
        f"quarantined={result.quarantine_count}"
    )
    if result.error:
        typer.echo(f"  error={result.error}")


# --- pipeline sub-app -----------------------------------------------------


@pipeline_app.command("run")
def pipeline_run(
    path: Path = typer.Argument(
        RAW_DIR, help="Excel file or directory (default: data/raw)."
    ),
    commit: bool = typer.Option(
        False, "--commit", help="Promote to committed/ immediately."
    ),
) -> None:
    """Step 4 — full pipeline: process + validate + diff + report + persist."""
    files = (
        discover_raw_files(path)
        if path.is_dir()
        else [path]
    )
    if not files:
        typer.echo(f"No Excel files found under {path}.")
        raise typer.Exit(code=1)

    mode = "commit" if commit else "dry-run"
    result = run_pipeline(files, mode=mode)
    typer.echo(f"Run {result.run_id} [{result.status}]")
    typer.echo(
        f"  rows_in={result.rows_in}  rows_out={result.rows_out}  "
        f"quarantined={result.quarantine_count}"
    )
    agg = result.aggregate_validation
    if agg is not None:
        verdict = "ACCEPTABLE" if agg.is_acceptable() else "NOT ACCEPTABLE"
        typer.echo(f"  validation: {verdict}")
        if not agg.is_acceptable():
            typer.echo(f"  failing: {', '.join(agg.critical_failures())}")
    if result.report_path:
        typer.echo(f"  report: {result.report_path}")


@pipeline_app.command("review")
def pipeline_review(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Open the state and report path for a run."""
    state = read_state(run_id)
    if state is None:
        typer.echo(f"Unknown run_id: {run_id}")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(state, indent=2, ensure_ascii=False, default=str))


@pipeline_app.command("commit")
def pipeline_commit(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Promote a dry-run to committed/ (Step 5 will layer DB load behind this)."""
    try:
        target = commit_run(run_id)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Commit failed: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(f"Committed -> {target}")


@pipeline_app.command("rollback")
def pipeline_rollback(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Move a committed run to rolled_back/ (Step 5 adds DB delete behind this)."""
    try:
        target = rollback_run(run_id)
    except FileNotFoundError as exc:
        typer.echo(f"Rollback failed: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(f"Rolled back -> {target}")


# --- quarantine sub-app ---------------------------------------------------


@quarantine_app.command("list")
def quarantine_list(run_id: str = typer.Option(..., "--run-id")) -> None:
    """List quarantined rows for a run."""
    rows = list_quarantined(run_id)
    if not rows:
        typer.echo(f"No quarantine records for {run_id}.")
        return
    typer.echo(f"{len(rows)} quarantined rows for {run_id}:")
    for row in rows[:50]:
        typer.echo(
            f"  [{row['severity']}] {row['source_file']}:{row['row_index']}  "
            f"{row['stage_failed']}: {row['fail_reason']}"
        )
    if len(rows) > 50:
        typer.echo(f"  ... and {len(rows) - 50} more.")


@quarantine_app.command("reprocess")
def quarantine_reprocess(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Re-run normalize on the quarantined rows with current rules.

    Writes a fresh run id with the now-passing rows under ``dry_run/`` and
    moves still-failing rows into that run's quarantine bucket. Use after
    fixing axioms / normalization rules.
    """
    summary = reprocess_quarantine(run_id)
    if summary["records"] == 0:
        typer.echo(f"No quarantine records for {run_id}.")
        return
    typer.echo(
        f"Reprocessed {summary['records']} records from {run_id} "
        f"-> {summary['new_run_id']}"
    )
    typer.echo(
        f"  now_pass={summary['now_pass']}  still_fail={summary['still_fail']}"
    )


# --- db sub-app ----------------------------------------------------------


@db_app.command("init")
def db_init() -> None:
    """Step 5 — create tables and (on Postgres) apply schema.sql."""
    engine = make_engine()
    init_db(engine)
    typer.echo(f"Initialized {engine.url}.")


@db_app.command("load")
def db_load(
    run_id: str = typer.Option(..., "--run-id"),
    embed: bool = typer.Option(False, "--embed", help="Also update embeddings."),
) -> None:
    """Load a committed run into Postgres (relational + optional vector)."""
    run_dir = COMMITTED_ROOT / run_id
    if not run_dir.exists():
        typer.echo(f"No committed run: {run_id}")
        raise typer.Exit(code=1)
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        result = load_run(session, run_id, run_dir)
        if embed:
            update_embeddings(session, run_id)
    typer.echo(f"Loaded {run_id}: {result.rows_inserted}")


@db_app.command("rollback")
def db_rollback(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Delete the change events / details / bom edges for ``run_id``."""
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        result = db_rollback_run(session, run_id)
    typer.echo(f"Rolled back {run_id}: {result.rows_deleted}")


@db_app.command("status")
def db_status() -> None:
    """List runs in preprocessing_runs."""
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        rows = session.execute(select(PreprocessingRun)).scalars().all()
    if not rows:
        typer.echo("No runs.")
        return
    for row in rows:
        typer.echo(
            f"  {row.run_id}  status={row.status}  rows={row.rows_inserted}"
        )


@db_app.command("verify")
def db_verify(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Per-table row counts for ``run_id`` (sanity check after load/rollback)."""
    engine = make_engine()
    Session = session_factory(engine)
    counts: dict[str, int] = {}
    with Session() as session:
        for label, model in (("parts", Part), ("models", Model), ("change_events", ChangeEvent)):
            counts[label] = (
                session.execute(
                    select(func.count()).select_from(model).where(model.run_id == run_id)
                ).scalar_one()
            )
    typer.echo(f"{run_id}: {counts}")


@app.command()
def search(
    query: str = typer.Argument(..., help="Free-text query."),
    top_k: int = typer.Option(10, "--top-k"),
    form_version: str = typer.Option(
        "", "--form-version", help="Optional VersionRAG filter."
    ),
) -> None:
    """Hybrid pgvector + pg_trgm search over change_events."""
    from src.db.search import hybrid_search

    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        hits = hybrid_search(
            session,
            query,
            top_k=top_k,
            form_version=form_version or None,
        )
    for hit in hits:
        typer.echo(
            f"  [{hit.score:.3f}] event_id={hit.event_id} "
            f"({hit.form_version}): {hit.change_point}"
        )


if __name__ == "__main__":
    app()
