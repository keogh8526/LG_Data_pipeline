"""Unified Typer CLI for the LG BOM preprocessing pipeline.

Run with ``python -m src.cli <command>``. Step 0-2 commands:
    inventory       scan raw Excel files into a parquet inventory
    classify        classify the form version of files
    schema-export   export the answer schema as JSON Schema
"""

from __future__ import annotations

from pathlib import Path

import typer

from src.preprocess.classify import classify_dir, classify_form
from src.preprocess.inventory import build_inventory
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


if __name__ == "__main__":
    app()
