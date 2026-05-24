"""Step 3 — deterministic value normalization (config-driven).

Each field declares a sequence of steps in ``config/normalization.yaml``. Steps
are pure functions over a single value; ``post_validate`` references an axiom
key (e.g. ``part_no``). Null-like inputs (None, NaN, "", " ", "N/A") short-circuit
to None and never raise.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field as dc_field
from functools import lru_cache
from typing import Any

import pandas as pd
import yaml

from src.ontology import axioms
from src.utils.paths import NORMALIZATION_PATH

# Strings (case-insensitive, trimmed) that mean "no value".
_NULL_TOKENS = frozenset({"", "n/a", "na", "null", "none", "-", "—"})

# Maps a 96col schema field name to its normalization rule key.
FIELD_TO_RULE: dict[str, str] = {
    "base_part_no": "part_no",
    "new_part_no": "part_no",
    "model_code": "model_code",
    "bom_level": "bom_level",
    "change_type": "change_type",
    "grade": "grade",
    "change_point": "change_point",
    "change_reason": "change_reason",
    "part_name": "part_name",
    "part_type": "part_type",
    "qty": "qty",
}


@dataclass
class NormalizationResult:
    """Outcome of normalizing a single value."""

    value: Any
    applied_steps: list[str] = dc_field(default_factory=list)
    success: bool = True
    fail_reason: str | None = None


@dataclass
class NormalizeReport:
    """Per-run summary of normalization failures."""

    rows: int
    failures: list[dict[str, Any]] = dc_field(default_factory=list)


def is_null(value: Any) -> bool:
    """Return True if ``value`` represents a missing entry.

    Args:
        value: Any raw value (None, NaN, str, number).
    """
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip().lower() in _NULL_TOKENS:
        return True
    return False


@lru_cache(maxsize=1)
def load_normalization_config() -> dict[str, Any]:
    """Load and cache ``config/normalization.yaml``.

    Returns:
        The parsed config dict.
    """
    return yaml.safe_load(NORMALIZATION_PATH.read_text(encoding="utf-8"))


def _coerce_step(step: object) -> dict[str, Any]:
    """Normalize a YAML step to a dict form."""
    return {"type": step} if isinstance(step, str) else dict(step)  # type: ignore[arg-type]


def _apply_step(value: Any, step: dict[str, Any]) -> Any:
    """Apply one normalization step to a single non-null value.

    Args:
        value: The current (non-null) value.
        step: The step config dict.

    Returns:
        The transformed value.

    Raises:
        ValueError: When the step fails (e.g. cast on a non-numeric string).
    """
    step_type = step["type"]
    if step_type == "strip":
        return str(value).strip()
    if step_type == "upper":
        return str(value).upper()
    if step_type == "lower":
        return str(value).lower()
    if step_type == "unicode_normalize":
        return unicodedata.normalize(step.get("form", "NFKC"), str(value))
    if step_type == "collapse_whitespace":
        return re.sub(r"\s+", " ", str(value))
    if step_type == "regex_remove":
        return re.sub(str(step["pattern"]), "", str(value))
    if step_type == "max_length":
        limit = int(step["value"])
        s = str(value)
        if len(s) <= limit:
            return s
        # Length policy: truncation is applied by the on_fail branch.
        raise ValueError(f"length {len(s)} > {limit}")
    if step_type == "cast":
        target = step["to"]
        if target == "int":
            return int(float(value))
        if target == "float":
            return float(value)
        if target == "str":
            return str(value)
        raise ValueError(f"unsupported cast target: {target}")
    if step_type == "range_check":
        v = float(value)
        low = float(step["min"])
        high = float(step["max"])
        if low <= v <= high:
            return value
        raise ValueError(f"value {v} outside [{low}, {high}]")
    if step_type == "map_alias":
        canonical: str | None
        if step["field"] == "change_type":
            canonical = axioms.normalize_change_type(str(value))
        elif step["field"] == "grade":
            canonical = axioms.normalize_grade(str(value))
        else:
            raise ValueError(f"no alias map for field: {step['field']}")
        if canonical is None:
            raise ValueError(f"unknown alias: {value!r}")
        return canonical
    raise ValueError(f"unknown step type: {step_type}")


def _apply_fail_policy(
    policy: str, value: Any, step: dict[str, Any]
) -> tuple[Any, bool]:
    """Apply an on_fail policy and return (recovered_value, recovered).

    Args:
        policy: ``quarantine``, ``truncate``, or ``set_null``.
        value: The value at the point of failure.
        step: The failing step config.

    Returns:
        ``(value, True)`` on successful recovery; ``(value, False)`` if the
        caller should treat the row as failed.
    """
    if policy == "set_null":
        return None, True
    if policy == "truncate":
        if step["type"] == "max_length":
            return str(value)[: int(step["value"])], True
        if step["type"] == "range_check":
            v = float(value)
            low = float(step["min"])
            high = float(step["max"])
            return max(low, min(high, v)), True
    return value, False


def normalize_field(
    value: Any, field_name: str, config: dict[str, Any] | None = None
) -> NormalizationResult:
    """Normalize one value against the rule for ``field_name``.

    Args:
        value: Raw input value.
        field_name: 96col schema field name (e.g. ``base_part_no``).
        config: Optional pre-loaded normalization config.

    Returns:
        A :class:`NormalizationResult`.
    """
    if is_null(value):
        return NormalizationResult(value=None)

    cfg = config if config is not None else load_normalization_config()
    rule_key = FIELD_TO_RULE.get(field_name, field_name)
    rules: dict[str, Any] | None = cfg["fields"].get(rule_key)
    if rules is None:
        return NormalizationResult(value=value)

    on_fail = rules.get("on_fail", "quarantine")
    applied: list[str] = []
    current = value
    for raw_step in rules.get("steps", []):
        step = _coerce_step(raw_step)
        try:
            current = _apply_step(current, step)
            applied.append(step["type"])
        except (ValueError, TypeError) as exc:
            recovered, ok = _apply_fail_policy(on_fail, current, step)
            if ok:
                current = recovered
                applied.append(f"{step['type']}({on_fail})")
                continue
            return NormalizationResult(
                value=current,
                applied_steps=applied,
                success=False,
                fail_reason=f"{step['type']}: {exc}",
            )

    post = rules.get("post_validate")
    if post:
        validator = getattr(axioms, f"validate_{post}", None)
        if validator is not None and not validator(current):
            return NormalizationResult(
                value=current,
                applied_steps=applied,
                success=False,
                fail_reason=f"post_validate({post}) failed",
            )

    return NormalizationResult(value=current, applied_steps=applied)


def normalize_dataframe(
    df: pd.DataFrame, run_id: str
) -> tuple[pd.DataFrame, NormalizeReport]:
    """Normalize every recognized column of ``df``.

    Args:
        df: Mapped DataFrame (96col-shaped).
        run_id: Run identifier for the failure report.

    Returns:
        ``(normalized_df, report)``. Failure reasons are appended to a
        ``_quarantine_reason`` column on the DataFrame.
    """
    cfg = load_normalization_config()
    out = df.copy()
    report = NormalizeReport(rows=len(out))
    existing_reasons = (
        out["_quarantine_reason"].fillna("").tolist()
        if "_quarantine_reason" in out.columns
        else [""] * len(out)
    )

    for column in [c for c in out.columns if c in FIELD_TO_RULE]:
        new_values: list[Any] = []
        for idx, value in enumerate(out[column].tolist()):
            result = normalize_field(value, column, cfg)
            new_values.append(result.value)
            if not result.success:
                reason = f"{column}: {result.fail_reason}"
                existing_reasons[idx] = (
                    f"{existing_reasons[idx]};{reason}" if existing_reasons[idx] else reason
                )
                report.failures.append(
                    {
                        "run_id": run_id,
                        "row": idx,
                        "field": column,
                        "raw": value,
                        "reason": result.fail_reason,
                    }
                )
        out[column] = new_values

    out["_quarantine_reason"] = [r if r else None for r in existing_reasons]
    return out, report
