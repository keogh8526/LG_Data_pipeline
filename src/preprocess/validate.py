"""v2.0 (D-011 후) Step 7 — 7 핵심 지표 검증.

이전 15+1 지표(통일성5/품질5/처리5/payload_preservation)에서 BOM Agent 시나리오에
필요한 7개로 축소:

  A. 통일성 (3): column_match, type_match, value_format_match, row_preservation
  B. 품질  (2): null_rate_required, axiom_violation_rate
  C. 처리  (관찰): rows_in, rows_out, rows_quarantined, rows_dropped, drop_reasons

threshold 완화 (실데이터 calibration 결과 반영):
  - value_format_match: 0.98 → 0.95
  - row_preservation: 0.95 → 0.90
  - null_rate_required_max: 0.01 → 0.05
  - axiom_violation_rate_max: 0.02 → 0.05

제거된 지표 (D-011): referential_integrity, duplicate_rate, outlier_rate,
null_rate_optional, payload_preservation_rate.
"""

from __future__ import annotations

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
    "value_format_match": 0.95,
    "row_preservation": 0.90,
    "null_rate_required_max": 0.05,
    "axiom_violation_rate_max": 0.05,
}


class ValidationReport(BaseModel):
    """processed run (또는 file)의 7 지표 측정 결과 (D-011 후)."""

    # A. 통일성
    column_match: float = 0.0
    type_match: float = 0.0
    value_format_match: float = 0.0
    row_preservation: float = 0.0

    # B. 품질
    null_rate_required: float = 0.0
    axiom_violation_rate: float = 0.0

    # C. 처리 (관찰만 — threshold 없음)
    rows_in: int = 0
    rows_out: int = 0
    rows_quarantined: int = 0
    rows_dropped: int = 0
    drop_reasons: dict[str, int] = Field(default_factory=dict)

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
        if self.row_preservation < THRESHOLDS["row_preservation"]:
            failures.append("row_preservation")
        if self.null_rate_required > THRESHOLDS["null_rate_required_max"]:
            failures.append("null_rate_required")
        if self.axiom_violation_rate > THRESHOLDS["axiom_violation_rate_max"]:
            failures.append("axiom_violation_rate")
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


def _measure_row_preservation(rows_in: int, rows_kept: int) -> float:
    return rows_kept / rows_in if rows_in else 1.0


def _null_rate(df: pd.DataFrame, fields: tuple[str, ...]) -> float:
    present = [f for f in fields if f in df.columns]
    if not present or df.empty:
        return 0.0
    total = len(df) * len(present)
    nulls = sum(int(df[f].isna().sum()) for f in present)
    return nulls / total


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
    """processed events DataFrame → 7 핵심 지표 측정 (D-011 후)."""
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
        row_preservation=_measure_row_preservation(raw_rows, len(df)),
        null_rate_required=_null_rate(df, REQUIRED_FIELDS),
        axiom_violation_rate=rows_quarantined / raw_rows if raw_rows else 0.0,
        rows_in=raw_rows,
        rows_out=rows_out,
        rows_quarantined=rows_quarantined,
        rows_dropped=max(0, raw_rows - len(df)),
        drop_reasons=_drop_reasons(df),
        file_path=file_path,
        form_version=form_version,
        run_id=run_id,
    )
