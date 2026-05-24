"""Step 3 — preprocessing pipeline orchestration.

Glues the deterministic stages together for a single raw file:

    classify -> extract -> map -> normalize -> resolve

The result is a 96col-shaped DataFrame plus a :class:`PreprocessResult` with
batch metadata. Step 4 builds dry-run / commit / rollback on top of this; this
module never writes to the DB.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.preprocess.classify import classify_form
from src.preprocess.extract import extract_rows
from src.preprocess.map import apply_mapping, load_mapping_rule
from src.preprocess.normalize import NormalizeReport, normalize_dataframe
from src.preprocess.resolve import resolve_models, resolve_parts
from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class PreprocessResult:
    """Outcome of preprocessing one raw file."""

    file_path: str
    status: str  # "ok" | "needs_human_classification" | "empty" | "error"
    run_id: str
    form_version: str | None = None
    df: pd.DataFrame | None = None
    rows_in: int = 0
    rows_out: int = 0
    quarantine_count: int = 0
    normalize_report: NormalizeReport | None = None
    error: str | None = None


def generate_run_id() -> str:
    """Return a fresh ``run_<UTC timestamp>_<short uuid>`` batch identifier."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"run_{stamp}_{uuid.uuid4().hex[:6]}"


def _attach_metadata(
    df: pd.DataFrame, file_path: Path, form_version: str, run_id: str
) -> pd.DataFrame:
    """Attach provenance columns to the processed DataFrame."""
    df = df.copy()
    df["source_file"] = str(file_path)
    df["form_version"] = form_version
    df["extracted_at"] = datetime.now(timezone.utc).isoformat()
    df["run_id"] = run_id
    return df


def preprocess_file(file_path: Path, run_id: str) -> PreprocessResult:
    """Run the deterministic preprocessing pipeline on one file.

    Args:
        file_path: Path to a raw Excel file.
        run_id: Batch identifier.

    Returns:
        A :class:`PreprocessResult`. ``status`` is one of:
            * ``"ok"`` — produced a processed DataFrame.
            * ``"needs_human_classification"`` — classifier returned unknown.
            * ``"empty"`` — no rows after extract.
            * ``"error"`` — unexpected failure during processing.
    """
    classification = classify_form(file_path)
    if classification.form_version == "unknown":
        return PreprocessResult(
            file_path=str(file_path),
            status="needs_human_classification",
            run_id=run_id,
        )

    try:
        rule = load_mapping_rule(classification.form_version)
        raw_df = extract_rows(file_path, rule)
    except Exception as exc:  # noqa: BLE001 — record and return
        log.warning("pipeline.extract_failed", file=file_path.name, error=str(exc))
        return PreprocessResult(
            file_path=str(file_path),
            status="error",
            run_id=run_id,
            form_version=classification.form_version,
            error=str(exc),
        )

    if raw_df.empty:
        return PreprocessResult(
            file_path=str(file_path),
            status="empty",
            run_id=run_id,
            form_version=classification.form_version,
        )

    mapped = apply_mapping(raw_df, rule)
    normalized, report = normalize_dataframe(mapped, run_id)
    resolved = resolve_models(resolve_parts(normalized))
    final_df = _attach_metadata(
        resolved, file_path, classification.form_version, run_id
    )

    quarantine_count = int(final_df["_quarantine_reason"].notna().sum())
    log.info(
        "pipeline.done",
        file=file_path.name,
        form=classification.form_version,
        rows_in=len(raw_df),
        rows_out=len(final_df),
        quarantine=quarantine_count,
    )
    return PreprocessResult(
        file_path=str(file_path),
        status="ok",
        run_id=run_id,
        form_version=classification.form_version,
        df=final_df,
        rows_in=len(raw_df),
        rows_out=len(final_df),
        quarantine_count=quarantine_count,
        normalize_report=report,
    )


@dataclass
class RunSummary:
    """Aggregate summary of a multi-file run."""

    run_id: str
    results: list[PreprocessResult] = field(default_factory=list)

    @property
    def rows_in(self) -> int:
        return sum(r.rows_in for r in self.results)

    @property
    def rows_out(self) -> int:
        return sum(r.rows_out for r in self.results)

    @property
    def quarantine_count(self) -> int:
        return sum(r.quarantine_count for r in self.results)


def preprocess_directory(directory: Path, run_id: str | None = None) -> RunSummary:
    """Preprocess every Excel file under a directory.

    Args:
        directory: Directory containing raw Excel files.
        run_id: Optional pre-allocated batch id (else generated).

    Returns:
        A :class:`RunSummary`.
    """
    run = run_id or generate_run_id()
    summary = RunSummary(run_id=run)
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
            summary.results.append(preprocess_file(path, run))
    return summary
