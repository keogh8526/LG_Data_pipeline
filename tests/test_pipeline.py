"""Tests for the Step 3/4 preprocessing pipeline orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.preprocess import pipeline as pipeline_mod
from src.preprocess.pipeline import (
    commit_run,
    generate_run_id,
    preprocess_directory,
    preprocess_file,
    read_state,
    rollback_run,
    run_pipeline,
)


@pytest.fixture
def redirected_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect every persistence path to ``tmp_path`` for one test."""
    processed = tmp_path / "processed"
    reports = tmp_path / "reports"
    quarantine = tmp_path / "quarantine"
    golden = tmp_path / "golden"
    for path in (processed, reports, quarantine, golden):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(pipeline_mod, "PROCESSED_DIR", processed)
    monkeypatch.setattr(pipeline_mod, "DRY_RUN_ROOT", processed / "dry_run")
    monkeypatch.setattr(pipeline_mod, "COMMITTED_ROOT", processed / "committed")
    monkeypatch.setattr(
        pipeline_mod, "ROLLED_BACK_ROOT", processed / "rolled_back"
    )
    monkeypatch.setattr(pipeline_mod, "REPORTS_DIR", reports)
    monkeypatch.setattr(pipeline_mod, "GOLDEN_DIR", golden)

    import src.preprocess.quarantine as q_mod

    monkeypatch.setattr(q_mod, "QUARANTINE_DIR", quarantine)
    return tmp_path


def test_run_id_is_unique_and_well_formed() -> None:
    a = generate_run_id()
    b = generate_run_id()
    assert a != b
    assert a.startswith("run_")


def test_preprocess_file_on_v12_fixture(fixture_workbooks: Path) -> None:
    result = preprocess_file(
        fixture_workbooks / "sample_v12.xlsx", run_id="run_test"
    )
    assert result.status == "ok"
    assert result.form_version == "v1_2"
    assert result.df is not None
    assert {"source_file", "form_version", "run_id"} <= set(result.df.columns)


def test_preprocess_directory_runs_every_fixture(fixture_workbooks: Path) -> None:
    summary = preprocess_directory(fixture_workbooks, run_id="run_test")
    statuses = {r.status for r in summary.results}
    assert statuses <= {"ok", "empty", "needs_human_classification"}
    assert len(summary.results) == 4


def test_preprocess_unknown_file_short_circuits(tmp_path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.active.append(["x"] * 40)
    path = tmp_path / "weird.xlsx"
    wb.save(path)

    result = preprocess_file(path, run_id="run_test")
    assert result.status == "needs_human_classification"


def test_run_pipeline_persists_dry_run_artifacts(
    fixture_workbooks: Path, redirected_paths: Path
) -> None:
    files = sorted(fixture_workbooks.glob("*.xlsx"))
    result = run_pipeline(files, mode="dry-run")
    assert result.status == "dry_run_complete"
    assert result.run_dir is not None
    assert (result.run_dir / "state.json").exists()
    assert (result.run_dir / "report.md").exists()
    # Report mirrored under data/reports/.
    assert result.report_path is not None and result.report_path.exists()


def test_commit_blocked_when_gate_fails(
    fixture_workbooks: Path, redirected_paths: Path
) -> None:
    # The synthetic fixtures yield very small DataFrames; the value_format_match
    # gate trips because the v1.2 fixture's column shape leaves required fields
    # null. The commit verb must refuse.
    files = sorted(fixture_workbooks.glob("*.xlsx"))
    result = run_pipeline(files, mode="dry-run")
    if result.aggregate_validation and result.aggregate_validation.is_acceptable():
        # If the synthetic data happens to pass, skip — this test only asserts
        # the gate when it fails, which is the realistic case for raw fixtures.
        pytest.skip("Synthetic data passed the gate; nothing to assert.")
    with pytest.raises(ValueError, match="commit blocked"):
        commit_run(result.run_id)


def test_commit_and_rollback_lifecycle(
    redirected_paths: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Bypass the gate to exercise the file-level lifecycle.
    run_id = "run_lifecycle"
    dry_dir = pipeline_mod.DRY_RUN_ROOT / run_id
    dry_dir.mkdir(parents=True)
    (dry_dir / "state.json").write_text(
        json.dumps(
            {"run_id": run_id, "status": "dry_run_complete", "acceptable": True}
        ),
        encoding="utf-8",
    )

    committed = commit_run(run_id)
    assert committed.exists()
    assert not dry_dir.exists()
    state = read_state(run_id)
    assert state is not None and state["status"] == "committed"

    rolled = rollback_run(run_id)
    assert rolled.exists()
    assert not committed.exists()
    state = read_state(run_id)
    assert state is not None and state["status"] == "rolled_back"


def test_commit_unknown_run_raises(redirected_paths: Path) -> None:
    with pytest.raises(FileNotFoundError):
        commit_run("does_not_exist")


def test_rollback_unknown_run_raises(redirected_paths: Path) -> None:
    with pytest.raises(FileNotFoundError):
        rollback_run("does_not_exist")
