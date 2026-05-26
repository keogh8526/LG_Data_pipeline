"""D-012 LG BOM 전처리 CLI.

``python -m src.cli <command>``. 명령 그룹:

    inventory                      raw 디렉토리 → file_inventory.parquet
    classify                       시트 단위 양식 분류
    narrativize                    단일 행 narrative 미리보기 (디버그)

    pipeline run [PATH]            classify→extract→normalize→narrativize→validate
                                   → dry_run/<run_id>/
    pipeline run --commit          + validation gate 통과 시 commit
    pipeline commit  --run-id ID   dry_run → committed
    pipeline rollback --run-id ID  committed → rolled_back

    quarantine list --run-id ID    격리된 행 조회

    db init                        ORM 테이블 + (Postgres) schema_dev_part_master.sql
    db load --run-id ID [--embed]  committed run → PG (source_files / ingestion_log /
                                   dev_part_master). --embed: embedding_dense 생성.
    db rollback --file-id ID       file_id 단위 DB 적재 원복 (CASCADE).
    db status                      ingestion_log 상태 요약.
    db verify [--file-id ID]       테이블별 카운트 출력.
    db reset --confirm             전체 데이터 삭제 (개발용).
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from sqlalchemy import delete, func, select

from src.db.engine import init_db, make_engine, session_factory
from src.db.load import load_run, update_embeddings
from src.db.models import DevPartMaster, IngestionLog, SourceFile
from src.db.rollback import rollback_file
from src.preprocess.classify import classify_dir, classify_file
from src.preprocess.inventory import build_inventory
from src.preprocess.pipeline import (
    COMMITTED_ROOT,
    commit_run,
    discover_raw_files,
    read_state,
    reprocess_quarantine,
    rollback_run,
    run_pipeline,
)
from src.preprocess.quarantine import list_quarantined
from src.utils.paths import INTERIM_DIR, RAW_DIR

app = typer.Typer(help="LG BOM D-012 전처리 CLI.")
pipeline_app = typer.Typer(help="dry-run / commit / rollback 사이클.")
quarantine_app = typer.Typer(help="격리된 행 조회.")
db_app = typer.Typer(help="PostgreSQL 적재 / 롤백 / 상태.")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(quarantine_app, name="quarantine")
app.add_typer(db_app, name="db")


# --- Top-level commands --------------------------------------------------


@app.command()
def inventory(
    raw_dir: Path = typer.Option(RAW_DIR, help="raw Excel 디렉토리."),
    output: Path = typer.Option(
        INTERIM_DIR / "file_inventory.parquet", help="출력 parquet."
    ),
) -> None:
    """raw 파일 인벤토리."""
    df = build_inventory(raw_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, index=False)
    if df.empty:
        typer.echo(f"No Excel files found under {raw_dir}.")
        return
    typer.echo(f"Inventory written to {output} ({len(df)} sheet rows).")


@app.command()
def classify(
    path: Path = typer.Argument(..., help="Excel 파일 또는 --all로 디렉토리."),
    all_files: bool = typer.Option(False, "--all", help="PATH를 디렉토리로 처리."),
) -> None:
    """시트 단위 양식 분류."""
    if all_files or path.is_dir():
        results = classify_dir(path)
        counts: dict[str, int] = {}
        for result in results:
            counts[result.form_version] = counts.get(result.form_version, 0) + 1
            flag = " [needs review]" if result.needs_review else ""
            typer.echo(
                f"{Path(result.file_path).name}: {result.form_version} "
                f"({result.confidence:.2f}){flag}  sheets={len(result.sheet_results)}"
            )
        typer.echo("\nForm-version counts:")
        for version, count in sorted(counts.items()):
            typer.echo(f"  {version}: {count}")
        return

    result, sheet_results = classify_file(path)
    typer.echo(f"{path.name}: {result.form_version} (confidence={result.confidence})")
    for sc in sheet_results:
        typer.echo(
            f"    {sc.sheet_name}: {sc.form_id} ({sc.confidence:.2f}) "
            f"max_col={sc.max_col} signals={','.join(sc.signals_matched)}"
        )


@app.command()
def narrativize(
    part_no: str = typer.Option(..., help="새 부품번호"),
    part_name: str = typer.Option("샘플 부품", help="부품명"),
    change_point: str = typer.Option("", help="변경점 자유텍스트"),
    change_reason: str = typer.Option("", help="변경 사유"),
    model_code: str = typer.Option("WSED7667M.ABMQEUR", help="모델 코드"),
    grade: str = typer.Option("Best-1", help="등급"),
    change_type: str = typer.Option("Change", help="New|Change|Carry-over"),
) -> None:
    """단일 row narrative 미리보기 (디버그용)."""
    from src.preprocess.narrativize import build_narrative

    dpm = {
        "part_no_new": part_no,
        "part_name": part_name,
        "new_model": model_code,
        "event": change_type,
        "change_point_raw": change_point or None,
        "change_reason_raw": change_reason or None,
    }
    extra = {"grade": grade}
    typer.echo(build_narrative(dpm, extra))


# --- pipeline sub-app ---------------------------------------------------


@pipeline_app.command("run")
def pipeline_run(
    path: Path = typer.Argument(RAW_DIR, help="Excel 파일 또는 디렉토리."),
    commit: bool = typer.Option(False, "--commit", help="validation 통과 시 commit."),
) -> None:
    """full pipeline run (6 steps)."""
    files = discover_raw_files(path) if path.is_dir() else [path]
    if not files:
        typer.echo(f"No Excel files found under {path}.")
        raise typer.Exit(code=1)
    mode = "commit" if commit else "dry-run"
    result = run_pipeline(files, mode=mode)
    typer.echo(f"Run {result.run_id} [{result.status}]")
    typer.echo(
        f"  rows_in={result.rows_in} rows_out={result.rows_out} "
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
    """run state.json 출력."""
    state = read_state(run_id)
    if state is None:
        typer.echo(f"Unknown run_id: {run_id}")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(state, indent=2, ensure_ascii=False, default=str))


@pipeline_app.command("commit")
def pipeline_commit(run_id: str = typer.Option(..., "--run-id")) -> None:
    """dry_run → committed (gate 통과 시)."""
    try:
        target = commit_run(run_id)
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(f"Commit failed: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(f"Committed -> {target}")


@pipeline_app.command("rollback")
def pipeline_rollback(run_id: str = typer.Option(..., "--run-id")) -> None:
    """committed → rolled_back."""
    try:
        target = rollback_run(run_id)
    except FileNotFoundError as exc:
        typer.echo(f"Rollback failed: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo(f"Rolled back -> {target}")


# --- quarantine sub-app -------------------------------------------------


@quarantine_app.command("list")
def quarantine_list(run_id: str = typer.Option(..., "--run-id")) -> None:
    """격리된 행 조회."""
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
    summary = reprocess_quarantine(run_id)
    typer.echo(json.dumps(summary, indent=2, ensure_ascii=False))


# --- db sub-app ---------------------------------------------------------


@db_app.command("init")
def db_init() -> None:
    """ORM 테이블 + (Postgres) schema_dev_part_master.sql 적용."""
    engine = make_engine()
    init_db(engine)
    typer.echo(f"Initialized {engine.url}.")


@db_app.command("load")
def db_load(
    run_id: str | None = typer.Option(None, "--run-id", help="committed run id"),
    embed: bool = typer.Option(False, "--embed", help="embedding_dense 생성 (Ollama 필요)"),
) -> None:
    """committed run → PostgreSQL (source_files + ingestion_log + dev_part_master)."""
    if not run_id:
        typer.echo("--run-id required.")
        raise typer.Exit(code=1)
    run_dir = COMMITTED_ROOT / run_id
    if not run_dir.exists():
        typer.echo(f"No committed run: {run_id}")
        raise typer.Exit(code=1)
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        result = load_run(session, run_dir)
        typer.echo(f"Loaded {run_id}: {result.rows_inserted}")
        if embed:
            n = update_embeddings(session, file_ids=result.file_ids)
            typer.echo(f"Embeddings updated: {n}")


@db_app.command("rollback")
def db_rollback(file_id: int = typer.Option(..., "--file-id")) -> None:
    """file_id 단위 DB 적재 원복 (CASCADE)."""
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        try:
            result = rollback_file(session, file_id)
        except ValueError as exc:
            typer.echo(f"Rollback failed: {exc}")
            raise typer.Exit(code=1) from exc
    typer.echo(f"Rolled back file_id={file_id}: {result.rows_deleted}")


@db_app.command("status")
def db_status() -> None:
    """ingestion_log 상태별 집계."""
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        rows = session.execute(
            select(IngestionLog.status, func.count())
            .group_by(IngestionLog.status)
        ).all()
    if not rows:
        typer.echo("No ingestion_log entries.")
        return
    for status, count in rows:
        typer.echo(f"  {status or '(null)'}: {count}")


@db_app.command("verify")
def db_verify(
    file_id: int | None = typer.Option(None, "--file-id"),
) -> None:
    """테이블별 row 카운트. --file-id 지정 시 해당 파일만."""
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        if file_id is None:
            sf = session.execute(select(func.count()).select_from(SourceFile)).scalar_one()
            log = session.execute(select(func.count()).select_from(IngestionLog)).scalar_one()
            dpm = session.execute(select(func.count()).select_from(DevPartMaster)).scalar_one()
            typer.echo(f"source_files: {sf}")
            typer.echo(f"ingestion_log: {log}")
            typer.echo(f"dev_part_master: {dpm}")
            return
        log = session.execute(
            select(func.count())
            .select_from(IngestionLog)
            .where(IngestionLog.file_id == file_id)
        ).scalar_one()
        dpm = session.execute(
            select(func.count())
            .select_from(DevPartMaster)
            .where(DevPartMaster.file_id == file_id)
        ).scalar_one()
        typer.echo(f"file_id {file_id}: ingestion_log={log}, dev_part_master={dpm}")


@db_app.command("reset")
def db_reset(
    confirm: bool = typer.Option(False, "--confirm", help="필수 확인 플래그"),
) -> None:
    """전체 데이터 삭제 (개발 편의용). 운영 환경 금지."""
    if not confirm:
        typer.echo("Refusing without --confirm.")
        raise typer.Exit(code=1)
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        session.execute(delete(DevPartMaster))
        session.execute(delete(IngestionLog))
        session.execute(delete(SourceFile))
        session.commit()
    typer.echo("All data deleted.")


if __name__ == "__main__":
    app()
