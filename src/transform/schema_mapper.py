"""Step 3 — schema mapping (legacy forms -> v1.2 answer schema).

Mapping rules live as human-readable YAML in ``ontology/mapping_rules/``. An LLM
*generates* candidate rules; this module *applies* them deterministically with
pandas. Rule generation has a deterministic fallback when no LLM is available.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import yaml

from ontology import axioms
from src.utils.logging import get_logger
from src.utils.paths import MAPPING_RULES_DIR

log = get_logger(__name__)

# The 10 fixed features that MUST have a mapping (target fields).
FIXED_TARGET_FIELDS: frozenset[str] = frozenset(
    {
        "base_part_no",
        "new_part_no",
        "part_name",
        "bom_level",
        "part_type",
        "change_type",
        "change_point",
        "change_reason",
        "qty",
        "common.base_model",
    }
)


@dataclass
class MappingEntry:
    """A single source-column -> target-field rule."""

    source_col: str
    target_field: str
    transformation: str
    confidence: float


@dataclass
class MappingRuleSet:
    """All mapping rules for one form version."""

    form_version: str
    header_row_offset: int
    mappings: list[MappingEntry]


def _to_int(value: object) -> object:
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_float(value: object) -> object:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _normalize_part_no(value: object) -> object:
    return axioms.normalize_part_no(value) if isinstance(value, str) else value


# Deterministic transformation registry — no LLM at apply time.
_TRANSFORMATIONS = {
    "identity": lambda v: v,
    "strip": _strip,
    "strip_whitespace": _strip,
    "uppercase": lambda v: v.upper() if isinstance(v, str) else v,
    "to_int": _to_int,
    "to_float": _to_float,
    "normalize_part_no": _normalize_part_no,
}


def load_rules(rule_file: Path) -> MappingRuleSet:
    """Load a mapping rule set from a YAML file.

    Args:
        rule_file: Path to a ``*_to_v12.yaml`` file.

    Returns:
        A parsed :class:`MappingRuleSet`.
    """
    data = yaml.safe_load(rule_file.read_text(encoding="utf-8"))
    mappings = [
        MappingEntry(
            source_col=m["source_col"],
            target_field=m["target_field"],
            transformation=m.get("transformation", "identity"),
            confidence=float(m.get("confidence", 1.0)),
        )
        for m in data.get("mappings", [])
    ]
    return MappingRuleSet(
        form_version=data["form_version"],
        header_row_offset=int(data.get("header_row_offset", 1)),
        mappings=mappings,
    )


def rules_for_version(form_version: str) -> Path:
    """Return the rule-file path for a form version.

    Args:
        form_version: e.g. ``"96col"``.

    Returns:
        Path to the matching YAML rule file.
    """
    return MAPPING_RULES_DIR / f"{form_version}_to_v12.yaml"


def check_fixed_coverage(rules: MappingRuleSet) -> set[str]:
    """Return fixed target fields that the rule set fails to cover.

    Args:
        rules: The rule set to check.

    Returns:
        Set of missing fixed target fields (empty when fully covered).
    """
    covered = {m.target_field for m in rules.mappings}
    return set(FIXED_TARGET_FIELDS) - covered


def apply_mapping(df: pd.DataFrame, rules: MappingRuleSet) -> pd.DataFrame:
    """Apply a mapping rule set to a source DataFrame, deterministically.

    Missing source columns are recorded as data errors (logged) rather than
    raised, so a single missing column never aborts the transform.

    Args:
        df: Source DataFrame whose columns are raw form headers.
        rules: The mapping rule set to apply.

    Returns:
        A DataFrame with v1.2 target-field column names.
    """
    out: dict[str, pd.Series] = {}
    for entry in rules.mappings:
        if entry.source_col not in df.columns:
            log.warning(
                "mapping.missing_source_col",
                form_version=rules.form_version,
                source_col=entry.source_col,
            )
            continue
        transform = _TRANSFORMATIONS.get(entry.transformation, _TRANSFORMATIONS["identity"])
        out[entry.target_field] = df[entry.source_col].map(transform)
    return pd.DataFrame(out)


def generate_rules_with_llm(
    form_version: str,
    headers: list[str],
) -> MappingRuleSet:
    """Generate candidate mapping rules for a form (LLM, with fallback).

    The LLM path is intentionally not wired up until an API key and real data
    are available. The deterministic fallback name-matches headers against the
    fixed feature labels, producing a low-confidence draft for human review.

    Args:
        form_version: The form version label.
        headers: Raw header strings from the source form.

    Returns:
        A draft :class:`MappingRuleSet` (human review required).
    """
    # TODO(real-data): call the LLM with header text + v1.2 field descriptions.
    label_to_field = {
        "base p/no": "base_part_no",
        "new p/no": "new_part_no",
        "part name": "part_name",
        "class desc.": "part_name",
        "bom level": "bom_level",
        "part type": "part_type",
        "구분": "change_type",
        "변경점": "change_point",
        "변경사유": "change_reason",
        "qty": "qty",
        "base model": "common.base_model",
    }
    mappings: list[MappingEntry] = []
    for header in headers:
        target = label_to_field.get(header.strip().lower())
        if target is None:
            continue
        mappings.append(
            MappingEntry(
                source_col=header,
                target_field=target,
                transformation="strip",
                confidence=0.6,
            )
        )
    log.info(
        "mapping.fallback_generated",
        form_version=form_version,
        matched=len(mappings),
    )
    return MappingRuleSet(form_version, header_row_offset=1, mappings=mappings)
