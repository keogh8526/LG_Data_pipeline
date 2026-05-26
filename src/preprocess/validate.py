"""v2.0 Step 7 — Pandera + Pydantic 검증 (15지표 + payload 보존률).

preprocessing_v2.md §14-1. v1.0의 15지표를 그대로 가져오되 기준 스키마가
96col → Core 13으로 변경. 추가로 ``payload_preservation_rate`` 측정 (v2.0 §14-4).

3개 그룹:
  A. 통일성 (5):  column_match, type_match, value_format_match,
                   referential_integrity, row_preservation
  B. 품질  (5):  null_rate_required, null_rate_optional, duplicate_rate,
                   axiom_violation_rate, outlier_rate
  C. 처리  (5):  rows_in, rows_out, rows_quarantined, rows_dropped, drop_reasons
  +    payload_preservation_rate (v2.0 신규)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from src.ontology import axioms

# v2.0 Core 13: 필수 vs 옵셔널
REQUIRED_FIELDS: tuple[str, ...] = (
    "part_no",
    "part_name",
    "new_model_code",
    "grade",
    "change_type",
)
OPTIONAL_FIELDS: tuple[str, ...] = (
    "base_part_no",
    "base_model_code",
    "region",
    "event_stage",
    "change_point",
    "change_reason",
    "bom_level",
    "part_type",
)
EXPECTED_FIELDS: tuple[str, ...] = REQUIRED_FIELDS + OPTIONAL_FIELDS

EXPECTED_DTYPES: dict[str, str] = {"bom_level": "Int64"}

THRESHOLDS: dict[str, float] = {
    "column_match": 1.0,
    "type_match": 1.0,
    "value_format_match": 0.98,
    "referential_integrity": 0.95,
    "row_preservation": 0.95,
    "null_rate_required_max": 0.01,
    "axiom_violation_rate_max": 0.02,
    "payload_preservation_rate": 1.0,  # 100% 보존 목표
}


class ValidationReport(BaseModel):
    """processed run (또는 file)의 15+1 지표 측정 결과."""

    # A. 통일성
    column_match: float = 0.0
    type_match: float = 0.0
    value_format_match: float = 0.0
    referential_integrity: float = 0.0
    row_preservation: float = 0.0

    # B. 품질
    null_rate_required: float = 0.0
    null_rate_optional: float = 0.0
    duplicate_rate: float = 0.0
    axiom_violation_rate: float = 0.0
    outlier_rate: float = 0.0

    # C. 처리
    rows_in: int = 0
    rows_out: int = 0
    rows_quarantined: int = 0
    rows_dropped: int = 0
    drop_reasons: dict[str, int] = Field(default_factory=dict)

    # v2.0 신규
    payload_preservation_rate: float = 1.0

    # Meta
    file_path: str | None = None
    form_version: str | None = None
    run_id: str
    processed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def is_acceptable(self) -> bool:
        return not self.critical_failures()

    def critical_failures(self) -> list[str]:
        failures: list[str] = []
        if self.column_match < THRESHOLDS["column_match"]:
            failures.append("column_match")
        if self.type_match < THRESHOLDS["type_match"]:
            failures.append("type_match")
        if self.value_format_match < THRESHOLDS["value_format_match"]:
            failures.append("value_format_match")
        if self.referential_integrity < THRESHOLDS["referential_integrity"]:
            failures.append("referential_integrity")
        if self.row_preservation < THRESHOLDS["row_preservation"]:
            failures.append("row_preservation")
        if self.null_rate_required > THRESHOLDS["null_rate_required_max"]:
            failures.append("null_rate_required")
        if self.axiom_violation_rate > THRESHOLDS["axiom_violation_rate_max"]:
            failures.append("axiom_violation_rate")
        if self.payload_preservation_rate < THRESHOLDS["payload_preservation_rate"]:
            failures.append("payload_preservation_rate")
        return failures


# --- Measurement helpers ----------------------------------------------


def _value_passes_format(value: object, field_name: str) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True
    text = str(value)
    if field_name in {"part_no", "base_part_no"}:
        return axioms.validate_part_no(text)
    if field_name in {"new_model_code", "base_model_code"}:
        return axioms.validate_model_code(text)
    if field_name == "change_type":
        return axioms.validate_change_type(text)
    if field_name == "grade":
        return axioms.validate_grade(text)
    if field_name == "bom_level":
        try:
            return axioms.validate_bom_level(int(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
    return True


def _measure_column_match(columns: list[str]) -> float:
    present = sum(1 for f in EXPECTED_FIELDS if f in columns)
    return present / len(EXPECTED_FIELDS)


def _measure_type_match(df: pd.DataFrame) -> float:
    if not EXPECTED_DTYPES:
        return 1.0
    ok = 0
    checked = 0
    for column in EXPECTED_DTYPES:
        if column not in df.columns:
            continue
        checked += 1
        series = df[column].dropna()
        if series.empty:
            ok += 1
            continue
        if column == "bom_level":
            if pd.api.types.is_integer_dtype(series) or all(isinstance(v, int) for v in series):
                ok += 1
    return ok / checked if checked else 1.0


def _measure_value_format_match(df: pd.DataFrame) -> float:
    checked = ok = 0
    for field in ("part_no", "base_part_no", "new_model_code", "change_type", "grade"):
        if field not in df.columns:
            continue
        for value in df[field].dropna().tolist():
            checked += 1
            if _value_passes_format(value, field):
                ok += 1
    return ok / checked if checked else 1.0


def _measure_referential_integrity(df: pd.DataFrame) -> float:
    if "new_model_code" not in df.columns:
        return 1.0
    series = df["new_model_code"].dropna()
    if series.empty:
        return 1.0
    ok = sum(1 for v in series if axioms.validate_model_code(str(v)))
    return ok / len(series)


def _measure_row_preservation(rows_in: int, rows_kept: int) -> float:
    return rows_kept / rows_in if rows_in else 1.0


def _null_rate(df: pd.DataFrame, fields: tuple[str, ...]) -> float:
    present = [f for f in fields if f in df.columns]
    if not present or df.empty:
        return 0.0
    total = len(df) * len(present)
    nulls = sum(int(df[f].isna().sum()) for f in present)
    return nulls / total


def _measure_duplicate_rate(df: pd.DataFrame) -> float:
    if df.empty or "part_no" not in df.columns:
        return 0.0
    keys = ["part_no"]
    if "new_model_code" in df.columns:
        keys.append("new_model_code")
    if "source_row" in df.columns:
        # row 자체는 source_row가 다르면 중복 아님 — exclude
        pass
    return float(df.duplicated(subset=keys).mean())


def _measure_outlier_rate(df: pd.DataFrame) -> float:
    flagged = total = 0
    if "bom_level" in df.columns:
        series = pd.to_numeric(df["bom_level"], errors="coerce").dropna()
        if not series.empty:
            total += len(series)
            flagged += int(((series < 0) | (series > 10)).sum())
    return flagged / total if total else 0.0


def _payload_has_content(value: object) -> bool:
    """payload 값이 비어있지 않은지 체크. dict 또는 JSON-string 둘 다 처리.

    pipeline.py가 parquet 저장 전 payload를 ``json.dumps``로 직렬화하므로
    DataFrame의 payload 컬럼은 str일 수 있다. validate가 두 형태 모두 인식해야
    실제 검증 (load.py가 다시 dict로 복원하기 전 단계에서도 정확한 측정).
    """
    if value is None:
        return False
    if isinstance(value, dict):
        return len(value) > 0
    if isinstance(value, str):
        s = value.strip()
        if not s or s in {"null", "{}", "[]"}:
            return False
        try:
            parsed = json.loads(s)
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
        return bool(parsed)
    return False


def _measure_payload_preservation(df: pd.DataFrame) -> float:
    """payload 컬럼이 모든 행에 존재 + non-empty인 비율.

    dict와 JSON-직렬화된 str 모두 인정 — pipeline.py가 parquet 저장 시
    ``json.dumps``로 변환하기 때문 (C-2 fix).
    """
    if "payload" not in df.columns:
        return 0.0 if not df.empty else 1.0
    if df.empty:
        return 1.0
    present = df["payload"].apply(_payload_has_content).sum()
    return float(present) / len(df)


def _drop_reasons(df: pd.DataFrame) -> dict[str, int]:
    if "_quarantine_reason" not in df.columns:
        return {}
    counts: dict[str, int] = {}
    for reason in df["_quarantine_reason"].dropna().tolist():
        for part in str(reason).split(";"):
            key = part.strip().split(":")[0].split("=")[0]
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


def validate_dataframe(
    df: pd.DataFrame,
    run_id: str,
    *,
    file_path: str | None = None,
    form_version: str | None = None,
    rows_in: int | None = None,
) -> ValidationReport:
    """processed events DataFrame → 15+1 지표 측정."""
    columns = list(df.columns)
    if "_quarantine_reason" in columns:
        quarantined_mask = df["_quarantine_reason"].notna()
    else:
        quarantined_mask = pd.Series(False, index=df.index) if not df.empty else pd.Series([], dtype=bool)
    rows_quarantined = int(quarantined_mask.sum())
    rows_out = len(df) - rows_quarantined
    raw_rows = rows_in if rows_in is not None else len(df)

    return ValidationReport(
        column_match=_measure_column_match(columns),
        type_match=_measure_type_match(df),
        value_format_match=_measure_value_format_match(df),
        referential_integrity=_measure_referential_integrity(df),
        row_preservation=_measure_row_preservation(raw_rows, len(df)),
        null_rate_required=_null_rate(df, REQUIRED_FIELDS),
        null_rate_optional=_null_rate(df, OPTIONAL_FIELDS),
        duplicate_rate=_measure_duplicate_rate(df),
        axiom_violation_rate=rows_quarantined / raw_rows if raw_rows else 0.0,
        outlier_rate=_measure_outlier_rate(df),
        rows_in=raw_rows,
        rows_out=rows_out,
        rows_quarantined=rows_quarantined,
        rows_dropped=max(0, raw_rows - len(df)),
        drop_reasons=_drop_reasons(df),
        payload_preservation_rate=_measure_payload_preservation(df),
        file_path=file_path,
        form_version=form_version,
        run_id=run_id,
    )
