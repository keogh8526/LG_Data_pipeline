"""Step 3 — apply a per-form mapping rule (deterministic, no LLM).

The rule lives in ``config/mapping_rules/<form>.yaml``. It declares the source
columns for each 96col answer-schema field, plus a small set of
transformations. Execution is pure-Python over a DataFrame: pick the
highest-priority source column present, run the transformations, and append
``_quarantine_reason`` for required-but-missing fields.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.ontology import axioms
from src.utils.paths import MAPPING_RULES_DIR


# --- Rule model -----------------------------------------------------------


@dataclass
class FieldMapping:
    """Mapping rule for a single 96col answer-schema field."""

    target: str
    sources: list[tuple[str, int]]
    transformations: list[dict[str, Any]]
    required: bool = False
    allowed: list[str] | None = None


@dataclass
class MappingRule:
    """Parsed mapping rule for one form version."""

    form_version: str
    header_row: int
    include_patterns: list[re.Pattern[str]]
    exclude_patterns: list[re.Pattern[str]]
    mappings: dict[str, FieldMapping] = dc_field(default_factory=dict)


def _compile_patterns(patterns: list[str] | None) -> list[re.Pattern[str]]:
    return [re.compile(p) for p in (patterns or [])]


def _coerce_transformation(item: object) -> dict[str, Any]:
    return {"type": item} if isinstance(item, str) else dict(item)  # type: ignore[arg-type]


def load_mapping_rule(form_version: str) -> MappingRule:
    """Load and parse the mapping rule for ``form_version``.

    Args:
        form_version: Form version key (e.g. ``"96col"``).

    Returns:
        A :class:`MappingRule`.
    """
    path = MAPPING_RULES_DIR / f"{form_version}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    sheet_filter = data.get("sheet_filter") or {}
    rule = MappingRule(
        form_version=data["form_version"],
        header_row=int(data.get("header_row", 1)),
        include_patterns=_compile_patterns(sheet_filter.get("include_patterns")),
        exclude_patterns=_compile_patterns(sheet_filter.get("exclude_patterns")),
    )
    for target, spec in data.get("mappings", {}).items():
        sources = [
            (str(s["column_name"]), int(s.get("priority", 1)))
            for s in spec.get("sources", [])
        ]
        sources.sort(key=lambda pair: pair[1])
        rule.mappings[target] = FieldMapping(
            target=target,
            sources=sources,
            transformations=[
                _coerce_transformation(t) for t in spec.get("transformations", [])
            ],
            required=bool(spec.get("required", False)),
            allowed=spec.get("allowed"),
        )
    return rule


# --- Transformation execution --------------------------------------------


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _apply_transformation(value: Any, trans: dict[str, Any]) -> Any:
    """Apply a single mapping-layer transformation to one value."""
    if _is_null(value):
        return None
    t = trans["type"]
    if t == "strip":
        return str(value).strip()
    if t == "upper":
        return str(value).upper()
    if t == "lower":
        return str(value).lower()
    if t == "regex_remove":
        return re.sub(str(trans["pattern"]), "", str(value))
    if t == "cast":
        target = trans["to"]
        try:
            if target == "int":
                return int(float(value))
            if target == "float":
                return float(value)
            return str(value)
        except (TypeError, ValueError):
            return None
    if t == "map_alias":
        field = trans["field"]
        if field == "change_type":
            return axioms.normalize_change_type(str(value)) or value
        if field == "grade":
            return axioms.normalize_grade(str(value)) or value
        return value
    return value


# --- Sheet selection ------------------------------------------------------


def sheet_passes(
    sheet_name: str, includes: list[re.Pattern[str]], excludes: list[re.Pattern[str]]
) -> bool:
    """Return True if a sheet name passes the include/exclude filters.

    Args:
        sheet_name: The sheet name to test.
        includes: Patterns the name must match (any). Empty = match all.
        excludes: Patterns that disqualify the name.
    """
    if any(p.search(sheet_name) for p in excludes):
        return False
    if not includes:
        return True
    return any(p.search(sheet_name) for p in includes)


# --- Application ---------------------------------------------------------


def _pick_source(columns: list[str], sources: list[tuple[str, int]]) -> str | None:
    """Return the first source column present in ``columns`` (by priority)."""
    for name, _priority in sources:
        if name in columns:
            return name
    return None


def apply_mapping(df: pd.DataFrame, rule: MappingRule) -> pd.DataFrame:
    """Apply a mapping rule to produce a 96col-shaped DataFrame.

    Args:
        df: Raw DataFrame as read from the source form.
        rule: Parsed mapping rule.

    Returns:
        A DataFrame with one column per ``rule.mappings`` target plus a
        ``_quarantine_reason`` column (semicolon-joined reasons per row, or
        None when the row mapped cleanly).
    """
    columns = list(df.columns)
    out_cols: dict[str, list[Any]] = {}
    quar: list[list[str]] = [[] for _ in range(len(df))]

    for target, spec in rule.mappings.items():
        source = _pick_source(columns, spec.sources)
        if source is None:
            out_cols[target] = [None] * len(df)
            if spec.required:
                for i in range(len(df)):
                    quar[i].append(f"{target}: no source column")
            continue

        values: list[Any] = df[source].tolist()
        for trans in spec.transformations:
            values = [_apply_transformation(v, trans) for v in values]
        out_cols[target] = values

        if spec.required:
            for i, v in enumerate(values):
                if _is_null(v):
                    quar[i].append(f"{target}: required empty")
        if spec.allowed:
            allowed_set = set(spec.allowed)
            for i, v in enumerate(values):
                if v is not None and str(v).strip() and str(v) not in allowed_set:
                    quar[i].append(f"{target}: {v!r} not in allowed")

    out = pd.DataFrame(out_cols)
    out["_quarantine_reason"] = [";".join(r) if r else None for r in quar]
    # Preserve source-sheet provenance if extract attached it.
    if "_source_sheet" in df.columns:
        out["_source_sheet"] = df["_source_sheet"].tolist()
    return out


__all__ = [
    "FieldMapping",
    "MappingRule",
    "apply_mapping",
    "load_mapping_rule",
    "sheet_passes",
]
