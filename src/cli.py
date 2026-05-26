"""v2.0 LG BOM 전처리 + 검색 CLI.

``python -m src.cli <command>``. 명령 그룹:

    inventory                      raw 디렉토리 → file_inventory.parquet
    classify                       시트 단위 양식 분류
    schema-export                  Core 13 schema → JSON Schema export
    narrativize                    단일 행 narrative 미리보기 (디버그)

    pipeline run [PATH]            classify→adapter→normalize→narrativize→validate
                                   → dry_run/<run_id>/
    pipeline run --commit          + validation gate 통과 시 commit
    pipeline commit  --run-id ID   dry_run → committed
    pipeline rollback --run-id ID  committed → rolled_back

    quarantine list --run-id ID    격리된 행 조회

    db init                        ORM 테이블 + (Postgres) schema.sql 적용
    db load --run-id ID [--embed]  committed run → PG 적재
    db rollback --run-id ID        DB 적재 원복
    db status                      run 상태 조회
    db verify --run-id ID          per-table count

    search "<query>" [--top-k 5]   Query Router + Hybrid + RRF + Rerank + Graph
"""

from __future__ import annotations

import json
from pathlib import Path

import typer
from sqlalchemy import func, select

from src.db.engine import init_db, make_engine, session_factory
from src.db.load import load_run, update_embeddings
from src.db.models import DevPartMaster, IngestionLog, SourceFile
from src.db.rollback import rollback_run as db_rollback_run  # stub; rewritten in Phase 5
from src.preprocess.classify import classify_dir, classify_file
from src.preprocess.inventory import build_inventory
from src.preprocess.pipeline import (
    COMMITTED_ROOT,
    commit_run,
    discover_raw_files,
    generate_run_id,
    read_state,
    reprocess_quarantine,
    rollback_run,
    run_pipeline,
)
from src.preprocess.quarantine import list_quarantined
from src.utils.paths import INTERIM_DIR, RAW_DIR, SCHEMA_JSON_PATH

app = typer.Typer(help="LG BOM v2.0 전처리 + 검색 CLI.")
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
    """Step 0 — raw 파일 인벤토리."""
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


@app.command()
def classify(
    path: Path = typer.Argument(..., help="Excel 파일 또는 --all로 디렉토리."),
    all_files: bool = typer.Option(False, "--all", help="PATH를 디렉토리로 처리."),
) -> None:
    """Step 1 — 시트 단위 양식 분류."""
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
    typer.echo("  per sheet:")
    for sc in sheet_results:
        typer.echo(
            f"    {sc.sheet_name}: {sc.form_id} ({sc.confidence:.2f}) "
            f"max_col={sc.max_col} signals={','.join(sc.signals_matched)}"
        )


@app.command("schema-export")
def schema_export(
    output: Path = typer.Option(SCHEMA_JSON_PATH, help="JSON Schema 출력 경로."),
) -> None:
    """Core 13 + ChangeEvent schema → JSON Schema."""
    from src.ontology.schema import export_schema_json

    export_schema_json(output)
    typer.echo(f"Schema exported to {output}.")


@app.command()
def narrativize(
    part_no: str = typer.Option(..., help="새 부품번호 (예: AGG74419321)"),
    part_name: str = typer.Option("샘플 부품", help="부품명"),
    change_point: str = typer.Option("", help="변경점 자유텍스트"),
    change_reason: str = typer.Option("", help="변경 사유"),
    model_code: str = typer.Option("WSED7667M.ABMQEUR", help="모델 코드"),
    grade: str = typer.Option("Best-1", help="등급"),
    change_type: str = typer.Option("Change", help="New|Change|Carry-over"),
) -> None:
    """단일 row narrative 미리보기 (디버그용)."""
    from src.preprocess.narrativize import narrativize as do_narr

    core = {
        "part_no": part_no,
        "part_name": part_name,
        "new_model_code": model_code,
        "grade": grade,
        "change_type": change_type,
        "change_point": change_point or None,
        "change_reason": change_reason or None,
    }
    typer.echo(do_narr(core, payload={}))


# --- pipeline sub-app ---------------------------------------------------


@pipeline_app.command("run")
def pipeline_run(
    path: Path = typer.Argument(RAW_DIR, help="Excel 파일 또는 디렉토리 (기본: data/raw)."),
    commit: bool = typer.Option(False, "--commit", help="validation 통과 시 commit."),
) -> None:
    """Step 3~7 — full pipeline run."""
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
    """run 상태 + report 경로 출력."""
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
    """ORM 테이블 + Postgres schema.sql 적용."""
    engine = make_engine()
    init_db(engine)
    typer.echo(f"Initialized {engine.url}.")


@db_app.command("load")
def db_load(
    run_id: str = typer.Option(..., "--run-id"),
    embed: bool = typer.Option(False, "--embed", help="multi-vector 임베딩까지 생성."),
) -> None:
    """committed run → PostgreSQL."""
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
    engine = make_engine()
    Session = session_factory(engine)
    with Session() as session:
        result = db_rollback_run(session, run_id)
    typer.echo(f"Rolled back {run_id}: {result.rows_deleted}")


@db_app.command("status")
def db_status() -> None:
    """ingestion_log 상태 요약 (D-012 Phase 6에서 본 구현 도입)."""
    typer.echo("db status is being reworked for dev_part_master in Phase 6.")


@db_app.command("verify")
def db_verify(file_id: int | None = typer.Option(None, "--file-id")) -> None:
    """테이블별 카운트 출력 (D-012 Phase 6에서 본 구현 도입)."""
    typer.echo("db verify is being reworked for dev_part_master in Phase 6.")


# --- agent-search (BOM Agent retrieve 데모용 — D-011 후) ----------------


@app.command("agent-search")
def agent_search(
    pno: str = typer.Option("", "--pno", help="부품번호 정확 매치 (part_no/base_part_no)."),
    part_name: str = typer.Option("", "--part-name", help="부품명 ILIKE 매치."),
    change_reason: str = typer.Option("", "--reason", help="변경사유 ILIKE 매치."),
    top_k: int = typer.Option(30, "--top-k"),
) -> None:
    """BOM Agent retrieve 노드가 호출할 단순 검색 데모.

    이전 v2.0 7-case Query Router + RRF + Rerank + Graph는 D-011 Phase D로
    제거. 본 명령은 ``src/db/search_simple.py:search_change_events``의 thin wrapper.
    """
    # D-012: search_simple.py 삭제됨. dev_part_master 검색은 팀원 ETL_PG 측 RAG가
    # 담당하며, 본 CLI는 적재 책임만 가짐. 임시 메시지만 출력.
    typer.echo(
        "agent-search was removed with the v2.0 search layer. "
        "Use dev_part_master + bge-m3 directly via psql or the ETL_PG side."
    )
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
