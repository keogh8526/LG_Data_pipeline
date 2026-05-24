"""Step 3/4 — preprocessing pipeline orchestration.

Per-file preprocessing (Step 3): ``classify -> extract -> map -> normalize ->
resolve``. Run-level dry-run / commit / rollback (Step 4) is built on top of
the per-file pipeline and lives entirely on the file system; Step 5 will add
the DB-load layer behind the same commit / rollback verbs.

Run lifecycle:

    dry_run/<run_id>/   <- always produced by `run_pipeline`
        files/*.parquet
        state.json
        report.md           (also copied to data/reports/<run_id>.md)
    committed/<run_id>/  <- after `commit_run`
    rolled_back/<run_id>/ <- after `rollback_run`
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pandas as pd

from src.preprocess.classify import classify_form
from src.preprocess.diff import DiffReport, diff_against_golden, load_golden
from src.preprocess.extract import extract_rows
from src.preprocess.map import apply_mapping, load_mapping_rule
from src.preprocess.normalize import NormalizeReport, normalize_dataframe
from src.preprocess.quarantine import (
    extract_quarantined,
    list_quarantined,
    save_quarantine,
)
from src.preprocess.report import build_markdown_report
from src.preprocess.resolve import resolve_models, resolve_parts
from src.preprocess.validate import ValidationReport, validate_dataframe
from src.utils.logging import get_logger
from src.utils.paths import GOLDEN_DIR, PROCESSED_DIR, REPORTS_DIR

log = get_logger(__name__)

# Filesystem layout for the run lifecycle.
DRY_RUN_ROOT = PROCESSED_DIR / "dry_run"
COMMITTED_ROOT = PROCESSED_DIR / "committed"
ROLLED_BACK_ROOT = PROCESSED_DIR / "rolled_back"

RunMode = Literal["dry-run", "commit"]
RunStatus = Literal["dry_run_complete", "committed", "rolled_back", "rejected"]


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


@dataclass
class RunResult:
    """The outcome of a multi-file pipeline run."""

    run_id: str
    status: RunStatus
    mode: RunMode
    results: list[PreprocessResult] = field(default_factory=list)
    aggregate_validation: ValidationReport | None = None
    file_validations: list[tuple[str, ValidationReport, DiffReport | None]] = field(
        default_factory=list
    )
    report_path: Path | None = None
    run_dir: Path | None = None

    @property
    def rows_in(self) -> int:
        return sum(r.rows_in for r in self.results)

    @property
    def rows_out(self) -> int:
        return sum(r.rows_out for r in self.results)

    @property
    def quarantine_count(self) -> int:
        return sum(r.quarantine_count for r in self.results)


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
        A :class:`PreprocessResult`.
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
        rows_out=len(final_df) - quarantine_count,
        quarantine=quarantine_count,
    )
    return PreprocessResult(
        file_path=str(file_path),
        status="ok",
        run_id=run_id,
        form_version=classification.form_version,
        df=final_df,
        rows_in=len(raw_df),
        rows_out=len(final_df) - quarantine_count,
        quarantine_count=quarantine_count,
        normalize_report=report,
    )


# --- Run lifecycle --------------------------------------------------------


def _persist_file(
    result: PreprocessResult, run_dir: Path
) -> tuple[ValidationReport, DiffReport | None]:
    """Save a processed file's parquet, quarantine, and per-file validation.

    Returns the validation report plus an optional golden-diff report.
    """
    file_stem = Path(result.file_path).stem
    files_dir = run_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    if result.df is not None:
        result.df.to_parquet(files_dir / f"{file_stem}.parquet", index=False)
        quarantined = extract_quarantined(result.df, result.run_id, result.file_path)
        save_quarantine(quarantined, result.run_id, file_stem)

    validation = validate_dataframe(
        result.df if result.df is not None else pd.DataFrame(),
        run_id=result.run_id,
        file_path=result.file_path,
        form_version=result.form_version,
        rows_in=result.rows_in,
    )

    diff_report: DiffReport | None = None
    if result.df is not None:
        golden = load_golden(GOLDEN_DIR, Path(result.file_path))
        if golden is not None:
            diff_report = diff_against_golden(result.df, golden)

    return validation, diff_report


def _aggregate_validation(
    results: list[PreprocessResult],
    file_validations: list[tuple[str, ValidationReport, DiffReport | None]],
    run_id: str,
) -> ValidationReport:
    """Build an aggregate validation by concatenating all processed DataFrames."""
    frames = [r.df for r in results if r.df is not None]
    if not frames:
        return ValidationReport(run_id=run_id)
    combined = pd.concat(frames, ignore_index=True)
    total_in = sum(r.rows_in for r in results)
    return validate_dataframe(
        combined, run_id=run_id, rows_in=total_in or len(combined)
    )


def _write_state(run_dir: Path, payload: dict[str, object]) -> Path:
    """Write ``state.json`` for a run directory."""
    path = run_dir / "state.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def run_pipeline(
    files: list[Path], mode: RunMode = "dry-run", run_id: str | None = None
) -> RunResult:
    """Process a batch of files end-to-end and persist a dry-run / commit.

    Args:
        files: Raw Excel files to process.
        mode: ``"dry-run"`` to save under ``dry_run/`` only; ``"commit"`` to
            additionally promote to ``committed/`` (Step 5 wires the DB load
            behind the same verb).
        run_id: Optional pre-allocated batch id (else generated).

    Returns:
        A :class:`RunResult` with paths and aggregated metrics.
    """
    run = run_id or generate_run_id()
    run_dir = DRY_RUN_ROOT / run
    run_dir.mkdir(parents=True, exist_ok=True)
    log.info("pipeline.run.start", run_id=run, mode=mode, files=len(files))

    results = [preprocess_file(path, run) for path in files]

    file_validations: list[tuple[str, ValidationReport, DiffReport | None]] = []
    audit_records: list[dict[str, object]] = []
    for result in results:
        validation, diff = _persist_file(result, run_dir)
        file_validations.append((result.file_path, validation, diff))
        if result.normalize_report and result.normalize_report.audit:
            for entry in result.normalize_report.audit.to_records():
                # Serialize raw before/after to strings so the parquet column
                # has a single dtype regardless of source field.
                entry["before"] = (
                    None if entry["before"] is None else str(entry["before"])
                )
                entry["after"] = (
                    None if entry["after"] is None else str(entry["after"])
                )
                entry["source_file"] = result.file_path
                audit_records.append(entry)

    if audit_records:
        pd.DataFrame(audit_records).to_parquet(
            run_dir / "audit.parquet", index=False
        )

    aggregate = _aggregate_validation(results, file_validations, run)

    report_path = build_markdown_report(
        run_id=run,
        file_reports=file_validations,
        aggregate=aggregate,
        output_dir=REPORTS_DIR,
    )
    # Mirror the report alongside the run dir for self-contained snapshots.
    shutil.copy(report_path, run_dir / "report.md")

    status: RunStatus = "dry_run_complete"
    _write_state(
        run_dir,
        {
            "run_id": run,
            "status": status,
            "mode": mode,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "files": [
                {
                    "path": r.file_path,
                    "status": r.status,
                    "form_version": r.form_version,
                    "rows_in": r.rows_in,
                    "rows_out": r.rows_out,
                    "quarantined": r.quarantine_count,
                }
                for r in results
            ],
            "acceptable": aggregate.is_acceptable(),
            "critical_failures": aggregate.critical_failures(),
        },
    )

    run_result = RunResult(
        run_id=run,
        status=status,
        mode=mode,
        results=results,
        aggregate_validation=aggregate,
        file_validations=file_validations,
        report_path=report_path,
        run_dir=run_dir,
    )

    if mode == "commit":
        commit_run(run)
        run_result.status = "committed"
        run_result.run_dir = COMMITTED_ROOT / run

    return run_result


def _find_run_dir(run_id: str) -> Path | None:
    """Return the existing directory for ``run_id`` (any lifecycle stage)."""
    for root in (DRY_RUN_ROOT, COMMITTED_ROOT, ROLLED_BACK_ROOT):
        candidate = root / run_id
        if candidate.exists():
            return candidate
    return None


def commit_run(run_id: str) -> Path:
    """Promote a dry-run to ``committed/``.

    Args:
        run_id: Batch identifier (must already be in ``dry_run/``).

    Returns:
        The new committed directory.

    Raises:
        FileNotFoundError: If no dry-run exists for ``run_id``.
        ValueError: If the run's validation gate failed (commit blocked).
    """
    source = DRY_RUN_ROOT / run_id
    if not source.exists():
        raise FileNotFoundError(f"no dry-run for {run_id}")

    state = json.loads((source / "state.json").read_text(encoding="utf-8"))
    if not state.get("acceptable"):
        raise ValueError(
            f"commit blocked: validation gate failed for {run_id} — "
            f"{state.get('critical_failures')}"
        )

    target = COMMITTED_ROOT / run_id
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))

    state["status"] = "committed"
    state["committed_at"] = datetime.now(timezone.utc).isoformat()
    _write_state(target, state)
    log.info("pipeline.commit", run_id=run_id, path=str(target))
    return target


def rollback_run(run_id: str) -> Path:
    """Move a committed run back to ``rolled_back/`` (file-level only).

    Step 5 layers the actual DB delete behind the same verb.

    Args:
        run_id: Batch identifier.

    Returns:
        The rolled-back directory.

    Raises:
        FileNotFoundError: If no committed run exists for ``run_id``.
    """
    source = COMMITTED_ROOT / run_id
    if not source.exists():
        raise FileNotFoundError(f"no committed run for {run_id}")

    target = ROLLED_BACK_ROOT / run_id
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))

    state_path = target / "state.json"
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else {"run_id": run_id}
    )
    state["status"] = "rolled_back"
    state["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    _write_state(target, state)
    log.info("pipeline.rollback", run_id=run_id, path=str(target))
    return target


def read_state(run_id: str) -> dict[str, object] | None:
    """Return the ``state.json`` payload for any lifecycle stage, or None."""
    run_dir = _find_run_dir(run_id)
    if run_dir is None:
        return None
    path = run_dir / "state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# --- Quarantine reprocess ------------------------------------------------


def reprocess_quarantine(run_id: str) -> dict[str, object]:
    """Re-run normalization on the quarantined rows of an existing run.

    Useful after a rule fix: re-attempt failed rows with the current
    ``axioms.yaml`` / ``normalization.yaml`` and split them into now-pass and
    still-fail buckets under a fresh run id.

    Args:
        run_id: The originating run whose quarantine to reprocess.

    Returns:
        A summary dict with ``new_run_id``, ``records``, ``now_pass``,
        ``still_fail``.
    """
    records = list_quarantined(run_id)
    if not records:
        log.info("pipeline.reprocess.empty", from_run=run_id)
        return {
            "new_run_id": None,
            "records": 0,
            "now_pass": 0,
            "still_fail": 0,
        }

    new_run = generate_run_id()
    new_run_dir = DRY_RUN_ROOT / new_run
    (new_run_dir / "files").mkdir(parents=True, exist_ok=True)

    by_file: dict[str, list[dict[str, object]]] = {}
    for record in records:
        raw = record["raw_row"]
        raw_dict = dict(raw) if not isinstance(raw, dict) else raw
        by_file.setdefault(str(record["source_file"]), []).append(raw_dict)

    total_now_pass = 0
    total_still_fail = 0
    timestamp = datetime.now(timezone.utc).isoformat()
    for source_file, rows in by_file.items():
        df = pd.DataFrame(rows)
        if "_quarantine_reason" in df.columns:
            df = df.drop(columns=["_quarantine_reason"])
        normalized, _report = normalize_dataframe(df, new_run)
        normalized["source_file"] = source_file
        normalized["run_id"] = new_run
        normalized["extracted_at"] = timestamp

        pass_mask = normalized["_quarantine_reason"].isna()
        passing = normalized[pass_mask].reset_index(drop=True)
        failing = normalized[~pass_mask].reset_index(drop=True)
        total_now_pass += len(passing)
        total_still_fail += len(failing)

        stem = Path(source_file).stem
        if not passing.empty:
            passing.to_parquet(
                new_run_dir / "files" / f"{stem}.parquet", index=False
            )
        if not failing.empty:
            still_fail_records = extract_quarantined(failing, new_run, source_file)
            save_quarantine(still_fail_records, new_run, stem)

    _write_state(
        new_run_dir,
        {
            "run_id": new_run,
            "status": "dry_run_complete",
            "mode": "reprocess",
            "started_at": timestamp,
            "reprocessed_from": run_id,
            "records": len(records),
            "now_pass": total_now_pass,
            "still_fail": total_still_fail,
            "acceptable": False,
        },
    )
    log.info(
        "pipeline.reprocess.done",
        from_run=run_id,
        new_run=new_run,
        now_pass=total_now_pass,
        still_fail=total_still_fail,
    )
    return {
        "new_run_id": new_run,
        "records": len(records),
        "now_pass": total_now_pass,
        "still_fail": total_still_fail,
    }


# --- Directory entry points ----------------------------------------------


@dataclass
class RunSummary:
    """Aggregate summary used by the lightweight ``preprocess`` CLI command."""

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
    """Run per-file preprocessing on a directory (no persistence).

    For the full dry-run / commit lifecycle use :func:`run_pipeline`.

    Args:
        directory: Directory containing raw Excel files.
        run_id: Optional pre-allocated batch id (else generated).

    Returns:
        A :class:`RunSummary` of per-file outcomes.
    """
    run = run_id or generate_run_id()
    summary = RunSummary(run_id=run)
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() in {".xlsx", ".xlsm", ".xls"}:
            summary.results.append(preprocess_file(path, run))
    return summary


def discover_raw_files(directory: Path) -> list[Path]:
    """List Excel files under ``directory`` (recursive)."""
    return [
        p
        for p in sorted(directory.rglob("*"))
        if p.suffix.lower() in {".xlsx", ".xlsm", ".xls"}
    ]
