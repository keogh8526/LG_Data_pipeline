"""Step 4 — validate a processed run against the 15 success metrics.

Measures fall into three groups:

  A. 통일성 (5): column_match, type_match, value_format_match,
                 referential_integrity, row_preservation
  B. 품질  (5): null_rate_required, null_rate_optional, duplicate_rate,
                 axiom_violation_rate, outlier_rate
  C. 처리  (5): rows_in, rows_out, rows_quarantined, rows_dropped, drop_reasons

A :class:`ValidationReport` provides ``is_acceptable()`` and
``critical_failures()`` for the dry-run / commit gate.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
from pydantic import BaseModel, Field

from src.ontology import axioms

# Fields the 96col answer schema marks as required (from the mapping rules).
REQUIRED_FIELDS: tuple[str, ...] = (
    "base_part_no",
    "part_name",
    "bom_level",
    "part_type",
    "change_type",
    "change_point",
    "model_code",
)
OPTIONAL_FIELDS: tuple[str, ...] = ("new_part_no", "change_reason", "qty")
EXPECTED_FIELDS: tuple[str, ...] = REQUIRED_FIELDS + OPTIONAL_FIELDS

# Expected pandas dtypes per field (None means "string or object is fine").
EXPECTED_DTYPES: dict[str, str] = {
    "bom_level": "Int64",
    "qty": "Float64",
}

# Acceptance thresholds — also referenced by Step 4's commit gate.
THRESHOLDS = {
    "column_match": 1.0,
    "type_match": 1.0,
    "value_format_match": 0.98,
    "referential_integrity": 0.95,
    "row_preservation": 0.95,
    "null_rate_required_max": 0.01,
    "axiom_violation_rate_max": 0.02,
}


class ValidationReport(BaseModel):
    """The 15-metric measurement of one processed file (or aggregate)."""

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

    # C. 처리 통계
    rows_in: int = 0
    rows_out: int = 0
    rows_quarantined: int = 0
    rows_dropped: int = 0
    drop_reasons: dict[str, int] = Field(default_factory=dict)

    # Meta
    file_path: str | None = None
    form_version: str | None = None
    run_id: str
    processed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def is_acceptable(self) -> bool:
        """Return True if every gate threshold is met."""
        return not self.critical_failures()

    def critical_failures(self) -> list[str]:
        """Names of the metrics that fall outside their acceptance threshold."""
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
        return failures


# --- Measurement primitives ----------------------------------------------


def _value_passes_format(value: object, field_name: str) -> bool:
    """Per-field axiom check used by value_format_match."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return True  # nulls are accounted for separately
    text = str(value)
    if field_name in {"base_part_no", "new_part_no"}:
        return axioms.validate_part_no(text)
    if field_name == "model_code":
        return axioms.validate_model_code(text)
    if field_name == "change_type":
        return axioms.validate_change_type(text)
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
    for column, _expected in EXPECTED_DTYPES.items():
        if column not in df.columns:
            continue
        checked += 1
        series = df[column]
        # Loose check: numeric or string-like as appropriate. Empty series
        # counts as a match (vacuously true).
        if series.dropna().empty:
            ok += 1
            continue
        if column in {"bom_level"}:
            if pd.api.types.is_integer_dtype(series) or all(
                isinstance(v, (int,)) for v in series.dropna()
            ):
                ok += 1
        elif column == "qty":
            if pd.api.types.is_float_dtype(series) or all(
                isinstance(v, (int, float)) for v in series.dropna()
            ):
                ok += 1
    return ok / checked if checked else 1.0


def _measure_value_format_match(df: pd.DataFrame) -> float:
    checked = 0
    ok = 0
    for field in ("base_part_no", "new_part_no", "model_code", "change_type"):
        if field not in df.columns:
            continue
        for value in df[field].dropna().tolist():
            checked += 1
            if _value_passes_format(value, field):
                ok += 1
    return ok / checked if checked else 1.0


def _measure_referential_integrity(df: pd.DataFrame) -> float:
    """Loose proxy: model_code values that parse cleanly via axioms."""
    if "model_code" not in df.columns:
        return 1.0
    series = df["model_code"].dropna()
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
    if df.empty or "base_part_no" not in df.columns:
        return 0.0
    keys = ["base_part_no"]
    if "model_code" in df.columns:
        keys.append("model_code")
    return float(df.duplicated(subset=keys).mean())


def _measure_outlier_rate(df: pd.DataFrame) -> float:
    """Combined out-of-range rate over qty + bom_level (plausibility check)."""
    flagged = 0
    total = 0
    if "qty" in df.columns:
        series = df["qty"].dropna()
        if not series.empty:
            total += len(series)
            flagged += int(((series < 0) | (series > 9999)).sum())
    if "bom_level" in df.columns:
        series = df["bom_level"].dropna()
        if not series.empty:
            total += len(series)
            flagged += int(((series < 0) | (series > 10)).sum())
    return flagged / total if total else 0.0


def _drop_reasons(df: pd.DataFrame) -> dict[str, int]:
    if "_quarantine_reason" not in df.columns:
        return {}
    counts: dict[str, int] = {}
    for reason in df["_quarantine_reason"].dropna().tolist():
        # Reasons are semicolon-joined "field: ..." entries.
        for part in str(reason).split(";"):
            key = part.strip().split(":")[0]
            if key:
                counts[key] = counts.get(key, 0) + 1
    return counts


# --- Top-level API --------------------------------------------------------


def validate_dataframe(
    df: pd.DataFrame,
    run_id: str,
    *,
    file_path: str | None = None,
    form_version: str | None = None,
    rows_in: int | None = None,
) -> ValidationReport:
    """Measure the 15 metrics over a processed DataFrame.

    Args:
        df: Final processed DataFrame (96col-shaped, with
            ``_quarantine_reason`` annotations).
        run_id: Batch identifier.
        file_path: Optional source file path for the report meta.
        form_version: Optional form version for the report meta.
        rows_in: Optional raw row count; defaults to ``len(df)``.

    Returns:
        A populated :class:`ValidationReport`.
    """
    columns = list(df.columns)
    quarantined_mask = (
        df["_quarantine_reason"].notna()
        if "_quarantine_reason" in columns
        else pd.Series(False, index=df.index)
    )
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
        file_path=file_path,
        form_version=form_version,
        run_id=run_id,
    )
