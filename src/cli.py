"""Unified Typer CLI for the LG BOM preprocessing pipeline.

Run with ``python -m src.cli <command>``. Step 0-3 commands:
    inventory       scan raw Excel files into a parquet inventory
    classify        classify the form version of files
    schema-export   export the answer schema as JSON Schema
    preprocess      run the deterministic preprocessing pipeline
"""

from __future__ import annotations

from pathlib import Path

import typer

from src.preprocess.classify import classify_dir, classify_form
from src.preprocess.inventory import build_inventory
from src.preprocess.pipeline import (
    generate_run_id,
    preprocess_directory,
    preprocess_file,
)
from src.utils.paths import INTERIM_DIR, RAW_DIR, SCHEMA_JSON_PATH

app = typer.Typer(help="LG BOM 전처리 파이프라인 CLI.")


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
    """Step 3 — classify, extract, map, normalize, resolve. Reports only."""
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


if __name__ == "__main__":
    app()
