"""v2.0 Step 3-8 — 전처리 파이프라인 orchestration.

per-file (실제로는 per-sheet) 전처리:
  classify_sheet → adapter dispatch → normalize → narrativize → validate

run-level dry_run / commit / rollback 사이클은 파일시스템 위에서 동작:

    dry_run/<run_id>/
        rows.parquet         (모든 ChangeEvent 행)
        bom.parquet          (BOM 어댑터의 (parts, edges))
        state.json
        report.md            → data/reports/<run_id>.md 사본
    committed/<run_id>/      (commit 후)
    rolled_back/<run_id>/    (rollback 후)
"""

from __future__ import annotations

import json
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pandas as pd

from pydantic import ValidationError

from src.ontology.schema import CoreFields
from src.preprocess.adapters import (
    ExtractedRow,
    extract_sheet,
)

# D-012 Phase 3: BomExtraction 제거 (BOM 어댑터도 ExtractedRow 스트림).
# 본 pipeline.py는 Phase 4에서 전면 재작성될 때까지 동작 보장 X.
# D-011: ProjectMeta 제거.
from src.preprocess.classify import classify_file
from src.preprocess.diff import DiffReport, diff_against_golden, load_golden
from src.preprocess.column_dict import load_column_dictionary
from src.preprocess.narrativize import narrativize
from src.preprocess.normalize import normalize_core
from src.preprocess.quarantine import (
    extract_quarantined,
    list_quarantined,
    save_quarantine,
)
from src.preprocess.report import build_markdown_report
from src.preprocess.resolve import resolve_models, resolve_parts
from src.preprocess.validate import ValidationReport, validate_dataframe
from src.utils.excel import read_workbook
from src.utils.logging import get_logger
from src.utils.paths import GOLDEN_DIR, PROCESSED_DIR, REPORTS_DIR

log = get_logger(__name__)

DRY_RUN_ROOT = PROCESSED_DIR / "dry_run"
COMMITTED_ROOT = PROCESSED_DIR / "committed"
ROLLED_BACK_ROOT = PROCESSED_DIR / "rolled_back"

RunMode = Literal["dry-run", "commit"]
RunStatus = Literal["dry_run_complete", "committed", "rolled_back", "rejected"]


# --- Result types --------------------------------------------------------


@dataclass
class FileResult:
    """파일 1개 처리 결과."""

    file_path: str
    status: str  # 'ok' | 'empty' | 'error' | 'needs_human_classification'
    run_id: str
    form_versions: list[str] = field(default_factory=list)
    rows_in: int = 0
    rows_out: int = 0
    quarantine_count: int = 0
    bom_parts: int = 0
    bom_edges: int = 0
    project_meta: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class RunResult:
    """run 결과."""

    run_id: str
    status: RunStatus
    mode: RunMode
    results: list[FileResult] = field(default_factory=list)
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
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"run_{stamp}_{uuid.uuid4().hex[:6]}"


# --- Per-file processing -------------------------------------------------


def _process_extracted_rows(
    rows: list[ExtractedRow],
    file_path: Path,
    form_id: str,
    run_id: str,
) -> tuple[list[dict[str, Any]], int, int, int]:
    """ExtractedRow 리스트 → DataFrame-ready dict 리스트 + 통계.

    Returns:
        (records, rows_in, rows_out, quarantined).
    """
    rows_in = len(rows)
    records: list[dict[str, Any]] = []
    quarantined = 0
    cdict = load_column_dictionary()

    for row in rows:
        core_norm, fails = normalize_core(row.core)

        # D-011 (B): extra_fields = Core 13에 매핑 안 된 원본 헤더만.
        # column_dict.lookup이 매핑한 헤더는 제외 → 저장 공간 + 검증 비용 축소.
        extra_fields: dict[str, Any] = {
            header: value
            for header, value in (row.payload or {}).items()
            if cdict.lookup(header) is None
        }

        # I-2: Pydantic CoreFields 검증 — 필수 필드 / enum / 정규식 통과 여부 확인.
        # core_norm의 None 값도 Pydantic에 명시 전달 (validator가 None을 UNKNOWN으로 변환).
        pydantic_reason: str | None = None
        try:
            CoreFields(**core_norm)
        except ValidationError as exc:
            err = exc.errors()[0] if exc.errors() else {}
            loc = ".".join(str(x) for x in err.get("loc", []))
            msg = err.get("msg", str(exc))
            pydantic_reason = f"core[{loc}]={msg}"

        reasons: list[str] = [f"{k}={v}" for k, v in fails.items()]
        if pydantic_reason:
            reasons.append(pydantic_reason)
        quarantine_reason = "; ".join(reasons) if reasons else None
        if quarantine_reason:
            quarantined += 1

        narrative = narrativize(core_norm, row.payload)
        record = {
            **core_norm,
            "extra_fields": extra_fields,
            "narrative_text": narrative,
            "form_version": row.source_meta.get("form_version", form_id),
            "source_file": row.source_meta.get("source_file", file_path.name),
            "source_sheet": row.source_meta.get("source_sheet", ""),
            "source_row": row.source_meta.get("source_row", 0),
            "run_id": run_id,
            "confidence": 1.0,
            "needs_review": bool(quarantine_reason),
            "_quarantine_reason": quarantine_reason,
        }
        records.append(record)

    rows_out = rows_in - quarantined
    return records, rows_in, rows_out, quarantined


def preprocess_file(file_path: Path, run_id: str) -> tuple[FileResult, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """파일 1개 → (FileResult, event records, parts records, edges records).

    한 파일의 모든 시트에 대해 분류 → 어댑터 dispatch → normalize → narrativize.
    """
    file_class, sheet_classes = classify_file(file_path)
    if file_class.error:
        return (
            FileResult(
                file_path=str(file_path),
                status="error",
                run_id=run_id,
                error=file_class.error,
            ),
            [],
            [],
            [],
        )

    event_records: list[dict[str, Any]] = []
    parts_records: list[dict[str, Any]] = []
    edge_records: list[dict[str, Any]] = []
    form_versions: set[str] = set()
    total_in = total_out = total_quar = 0
    project_meta: dict[str, Any] | None = None

    try:
        sheets = read_workbook(file_path)
    except Exception as exc:  # noqa: BLE001
        return (
            FileResult(
                file_path=str(file_path),
                status="error",
                run_id=run_id,
                error=f"read_workbook: {exc}",
            ),
            [],
            [],
            [],
        )

    sheet_idx = {s.name: s for s in sheets}
    for sc in sheet_classes:
        if sc.form_id == "unknown":
            continue
        sheet = sheet_idx.get(sc.sheet_name)
        if sheet is None:
            continue

        file_meta = {"run_id": run_id, "form_id": sc.form_id}
        result = extract_sheet(file_path, sheet, sc.form_id, file_meta)

        if isinstance(result, BomExtraction):
            parts_records.extend(result.parts)
            edge_records.extend(result.bom_edges)
            form_versions.add(sc.form_id)
            continue

        # D-011: ProjectMeta 분기 제거 — activity_master_meta는 unknown으로 빠짐.

        # list[ExtractedRow]
        records, rows_in, rows_out, quar = _process_extracted_rows(
            result, file_path, sc.form_id, run_id
        )
        event_records.extend(records)
        form_versions.add(sc.form_id)
        total_in += rows_in
        total_out += rows_out
        total_quar += quar

    status = "ok" if (event_records or parts_records or project_meta) else "empty"
    file_result = FileResult(
        file_path=str(file_path),
        status=status,
        run_id=run_id,
        form_versions=sorted(form_versions),
        rows_in=total_in,
        rows_out=total_out,
        quarantine_count=total_quar,
        bom_parts=len(parts_records),
        bom_edges=len(edge_records),
        project_meta=project_meta,
    )
    log.info(
        "pipeline.file_done",
        file=file_path.name,
        rows_in=total_in,
        rows_out=total_out,
        quarantine=total_quar,
        bom_parts=len(parts_records),
        bom_edges=len(edge_records),
    )
    return file_result, event_records, parts_records, edge_records


# --- Run lifecycle -------------------------------------------------------


def _write_state(run_dir: Path, payload: dict[str, Any]) -> Path:
    path = run_dir / "state.json"
    path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    return path


def read_state(run_id: str) -> dict[str, Any] | None:
    """run_id에 해당하는 state.json 로드 (dry_run/committed/rolled_back 중 검색)."""
    for root in (DRY_RUN_ROOT, COMMITTED_ROOT, ROLLED_BACK_ROOT):
        path = root / run_id / "state.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return None


def discover_raw_files(directory: Path) -> list[Path]:
    """디렉토리에서 엑셀 파일 모두 수집."""
    suffixes = {".xlsx", ".xlsm", ".xls"}
    return sorted(p for p in directory.rglob("*") if p.suffix.lower() in suffixes)


def run_pipeline(
    files: list[Path],
    mode: RunMode = "dry-run",
    run_id: str | None = None,
) -> RunResult:
    """전체 파이프라인 1회 run.

    files를 처리해 dry_run/<run_id>/에 저장. mode='commit'이면 committed/로
    승격 (validation gate 통과 시).
    """
    run = run_id or generate_run_id()
    run_dir = DRY_RUN_ROOT / run
    run_dir.mkdir(parents=True, exist_ok=True)
    log.info("pipeline.run.start", run_id=run, mode=mode, files=len(files))

    file_results: list[FileResult] = []
    all_events: list[dict[str, Any]] = []
    all_parts: list[dict[str, Any]] = []
    all_edges: list[dict[str, Any]] = []
    project_metas: list[dict[str, Any]] = []

    for path in files:
        fr, events, parts, edges = preprocess_file(path, run)
        file_results.append(fr)
        all_events.extend(events)
        all_parts.extend(parts)
        all_edges.extend(edges)
        if fr.project_meta:
            project_metas.append({"source_file": path.name, **fr.project_meta})

    # I-3: Entity Resolution 단계 — 모든 어댑터가 끝난 후 일괄 ER.
    # 결과는 별도 parquet(`resolved.parquet`)로 저장 → load.py가 parts.aliases 등에 활용.
    resolution_summary = _run_entity_resolution(all_events, all_parts)

    # persist — extra_fields는 JSON-serialize해서 parquet schema 갈등 회피 (D-011 B)
    events_df = pd.DataFrame(all_events) if all_events else pd.DataFrame()
    bom_df = pd.DataFrame(all_parts + all_edges) if (all_parts or all_edges) else pd.DataFrame()
    if not events_df.empty:
        if "extra_fields" in events_df.columns:
            events_df["extra_fields"] = events_df["extra_fields"].apply(
                lambda v: json.dumps(v, ensure_ascii=False, default=str)
                if v is not None
                else None
            )
        events_df.to_parquet(run_dir / "rows.parquet", index=False)
        if "_quarantine_reason" in events_df.columns:
            quarantined = events_df[events_df["_quarantine_reason"].notna()]
            if not quarantined.empty:
                for source_file, group in quarantined.groupby("source_file"):
                    q_records = extract_quarantined(group, run, str(source_file))
                    save_quarantine(q_records, run, Path(str(source_file)).stem)
    if all_parts:
        pd.DataFrame(all_parts).to_parquet(run_dir / "parts.parquet", index=False)
    if all_edges:
        pd.DataFrame(all_edges).to_parquet(run_dir / "edges.parquet", index=False)
    # BOM 어댑터의 parts+edges를 하나의 bom.parquet으로 함께 묶기 (load.py가 두 컬럼셋 모두 읽음)
    if all_edges:
        pd.DataFrame(all_edges).to_parquet(run_dir / "bom.parquet", index=False)

    # I-3: ER 결과 저장 — part_no/model_code/supplier/part_name별 canonical/aliases 사전.
    if resolution_summary:
        with open(run_dir / "resolved.json", "w", encoding="utf-8") as f:
            json.dump(resolution_summary, f, ensure_ascii=False, indent=2, default=str)

    # validate
    aggregate = validate_dataframe(
        events_df,
        run_id=run,
        rows_in=sum(r.rows_in for r in file_results) or len(events_df),
    )

    # golden diff (per file)
    file_validations: list[tuple[str, ValidationReport, DiffReport | None]] = []
    for fr in file_results:
        file_df = events_df[events_df["source_file"] == Path(fr.file_path).name] if not events_df.empty else pd.DataFrame()
        v = validate_dataframe(
            file_df,
            run_id=run,
            file_path=fr.file_path,
            form_version=",".join(fr.form_versions),
            rows_in=fr.rows_in,
        )
        diff_report: DiffReport | None = None
        golden = load_golden(GOLDEN_DIR, Path(fr.file_path))
        if golden is not None and not file_df.empty:
            diff_report = diff_against_golden(file_df, golden)
        file_validations.append((fr.file_path, v, diff_report))

    # report
    report_path = build_markdown_report(
        run_id=run,
        file_reports=file_validations,
        aggregate=aggregate,
        output_dir=REPORTS_DIR,
    )
    shutil.copy(report_path, run_dir / "report.md")

    acceptable = aggregate.is_acceptable() if aggregate else False
    status: RunStatus = "dry_run_complete"
    if mode == "commit":
        if acceptable:
            status = "committed"
            COMMITTED_ROOT.mkdir(parents=True, exist_ok=True)
            target = COMMITTED_ROOT / run
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(run_dir, target)
        else:
            status = "rejected"

    # C-4: ValidationReport.model_dump()은 메서드를 직렬화하지 않으므로
    # critical_failures + is_acceptable을 명시적으로 dict에 박아 commit_run gate가
    # state.json만으로 판단 가능하게 함.
    aggregate_dict: dict[str, Any] | None = None
    if aggregate:
        aggregate_dict = aggregate.model_dump()
        aggregate_dict["critical_failures"] = aggregate.critical_failures()
        aggregate_dict["is_acceptable"] = aggregate.is_acceptable()

    _write_state(
        run_dir,
        {
            "run_id": run,
            "status": status,
            "mode": mode,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "files": [fr.__dict__ for fr in file_results],
            "rows_in": sum(r.rows_in for r in file_results),
            "rows_out": sum(r.rows_out for r in file_results),
            "quarantine_count": sum(r.quarantine_count for r in file_results),
            "aggregate_validation": aggregate_dict,
            "report_path": str(report_path),
            "project_metas": project_metas,
        },
    )

    log.info(
        "pipeline.run.done",
        run_id=run,
        status=status,
        events=len(all_events),
        parts=len(all_parts),
        edges=len(all_edges),
    )

    return RunResult(
        run_id=run,
        status=status,
        mode=mode,
        results=file_results,
        aggregate_validation=aggregate,
        file_validations=file_validations,
        report_path=report_path,
        run_dir=run_dir,
    )


# --- Entity Resolution helper (D-011 Phase C: 단순화) ------------------


def _run_entity_resolution(
    events: list[dict[str, Any]],
    parts: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """D-011: ER 단순화 — parts/models canonical_id 집합만 반환.

    이전 4종 (parts/models/part_names/suppliers) 3-band 분류는 제거.
    fuzzy 매칭이 필요해지면 별도 모듈로 재도입.

    Returns:
        ``{"parts": [...], "models": [...]}`` — 각 canonical_id list.
    """
    raw_part_nos: list[str] = []
    for e in events:
        for k in ("part_no", "base_part_no"):
            if e.get(k):
                raw_part_nos.append(str(e[k]))
    for p in parts:
        if p.get("part_no"):
            raw_part_nos.append(str(p["part_no"]))

    raw_models: list[str] = []
    for e in events:
        for k in ("new_model_code", "base_model_code"):
            if e.get(k):
                raw_models.append(str(e[k]))

    return {
        "parts": sorted(resolve_parts(raw_part_nos)),
        "models": sorted(resolve_models(raw_models)),
    }


# --- commit / rollback --------------------------------------------------


def _resolve_critical_failures(agg: dict[str, Any] | None) -> list[str]:
    """state.json의 aggregate_validation에서 critical_failures 결정.

    C-4 fix: state.json에 ``critical_failures`` 가 명시되면 그대로 사용.
    예전 형식(키 누락)을 만나면 ValidationReport를 재구성해 실시간 평가.
    """
    if not agg or not isinstance(agg, dict):
        return []
    if "critical_failures" in agg:
        return list(agg.get("critical_failures") or [])
    # Fallback — 옛 state.json은 critical_failures 키 없음. ValidationReport 재구성.
    try:
        report = ValidationReport(**{k: v for k, v in agg.items() if k in ValidationReport.model_fields})
        return report.critical_failures()
    except Exception:  # noqa: BLE001
        return []


def commit_run(run_id: str) -> Path:
    """dry_run/<run_id> → committed/<run_id> 승격.

    validation gate가 통과해야만 승격. critical_failures 가 비어있지 않으면 raise.
    """
    src = DRY_RUN_ROOT / run_id
    if not src.exists():
        raise FileNotFoundError(f"no dry_run for {run_id}")
    state = read_state(run_id) or {}
    failing = _resolve_critical_failures(state.get("aggregate_validation"))
    if failing:
        raise ValueError(f"commit blocked - failing metrics: {failing}")
    target = COMMITTED_ROOT / run_id
    COMMITTED_ROOT.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(src, target)
    state["status"] = "committed"
    state["committed_at"] = datetime.now(timezone.utc).isoformat()
    (target / "state.json").write_text(json.dumps(state, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    return target


def rollback_run(run_id: str) -> Path:
    """committed/<run_id> → rolled_back/<run_id>."""
    src = COMMITTED_ROOT / run_id
    if not src.exists():
        raise FileNotFoundError(f"no committed run for {run_id}")
    target = ROLLED_BACK_ROOT / run_id
    ROLLED_BACK_ROOT.mkdir(parents=True, exist_ok=True)
    if target.exists():
        shutil.rmtree(target)
    shutil.move(str(src), str(target))
    state = read_state(run_id) or {}
    state["status"] = "rolled_back"
    state["rolled_back_at"] = datetime.now(timezone.utc).isoformat()
    (target / "state.json").write_text(json.dumps(state, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    return target


def reprocess_quarantine(run_id: str) -> dict[str, Any]:
    """quarantine된 row를 현재 룰로 재처리. (간단한 stub — 본격 구현은 v1.5)"""
    records = list_quarantined(run_id)
    if not records:
        return {"records": 0, "new_run_id": None, "now_pass": 0, "still_fail": 0}
    # quarantine은 raw_row를 보존하므로 normalize 재실행 가능 — MVP에선 카운트만.
    return {
        "records": len(records),
        "new_run_id": None,
        "now_pass": 0,
        "still_fail": len(records),
    }
