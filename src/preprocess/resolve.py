"""Step 3 — entity resolution (parts, models, suppliers).

Deterministic, no LLM. The goal is to collapse string variants that refer to
the same real-world entity into a single canonical form.

* ``resolve_parts``   — same normalized part number = same part.
* ``parse_model_code`` — split ``"WSED7667M.ABMQEUR"`` into model / grade /
  region components for downstream lookups.
* ``resolve_suppliers`` — rapidfuzz-based fuzzy clustering with a threshold.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from src.ontology import axioms

# Model-code format: name + optional `.<suffix>`. The suffix's trailing 2-3
# letters are the region code by LG convention; what comes before is the
# grade / buyer suffix.
_MODEL_CODE_RE = re.compile(r"^([A-Z]{2,5}\d{3,5}[A-Z]?)(?:\.([A-Z0-9]+))?$")
_REGION_CODES: frozenset[str] = frozenset(
    {"EUR", "EUE", "EAP", "SJ", "SA", "AU"}
)


@dataclass
class ParsedModel:
    """Parsed components of a model code."""

    raw: str
    model_name: str
    grade_suffix: str | None
    region: str | None


def parse_model_code(code: str) -> ParsedModel | None:
    """Parse a model code into its named components.

    Args:
        code: Raw model code (e.g. ``"WSED7667M.ABMQEUR"``).

    Returns:
        A :class:`ParsedModel`, or None if the code does not match the format.
    """
    if not isinstance(code, str):
        return None
    cleaned = code.strip().upper()
    match = _MODEL_CODE_RE.match(cleaned)
    if not match:
        return None
    name = match.group(1)
    suffix = match.group(2)

    region: str | None = None
    grade_suffix: str | None = None
    if suffix:
        for length in (3, 2):
            tail = suffix[-length:]
            if tail in _REGION_CODES:
                region = tail
                grade_suffix = suffix[:-length] or None
                break
        if region is None:
            grade_suffix = suffix
    return ParsedModel(
        raw=code, model_name=name, grade_suffix=grade_suffix, region=region
    )


def resolve_parts(df: pd.DataFrame) -> pd.DataFrame:
    """Add a canonical part-number column for downstream deduplication.

    Args:
        df: Normalized DataFrame. Must include ``base_part_no``.

    Returns:
        A copy of ``df`` with ``canonical_part_no`` populated where
        ``base_part_no`` is a non-null string.
    """
    out = df.copy()

    def _canon(value: object) -> str | None:
        if value is None or not isinstance(value, str) or not value.strip():
            return None
        return axioms.normalize_part_no(value)

    out["canonical_part_no"] = out["base_part_no"].map(_canon)
    return out


def resolve_models(df: pd.DataFrame) -> pd.DataFrame:
    """Annotate each row with the parsed model components.

    Args:
        df: Normalized DataFrame. Must include ``model_code``.

    Returns:
        A copy of ``df`` with ``model_name``, ``model_grade_suffix``, and
        ``model_region`` columns added.
    """
    out = df.copy()
    parsed = out["model_code"].map(
        lambda x: parse_model_code(x) if isinstance(x, str) else None
    )
    out["model_name"] = parsed.map(lambda p: p.model_name if p else None)
    out["model_grade_suffix"] = parsed.map(
        lambda p: p.grade_suffix if p else None
    )
    out["model_region"] = parsed.map(lambda p: p.region if p else None)
    return out


def resolve_suppliers(
    values: list[str], threshold: float = 0.9
) -> dict[str, str]:
    """Cluster supplier strings via rapidfuzz, case- and whitespace-insensitive.

    Args:
        values: Raw supplier strings (duplicates and Nones tolerated).
        threshold: Similarity threshold in 0..1 for clustering.

    Returns:
        Mapping ``{raw_value -> canonical}`` where the longest member of each
        cluster (in raw form) is chosen as the canonical.
    """
    from rapidfuzz import fuzz

    def _norm(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())

    raw_values = sorted({v for v in values if isinstance(v, str) and v.strip()})
    cutoff = threshold * 100
    groups: list[tuple[str, list[str]]] = []  # (normalized representative, members)

    for raw in raw_values:
        normalized = _norm(raw)
        best_index: int | None = None
        best_score = 0.0
        for index, (rep, _members) in enumerate(groups):
            score = fuzz.ratio(normalized, rep)
            if score >= cutoff and score > best_score:
                best_index = index
                best_score = score
        if best_index is None:
            groups.append((normalized, [raw]))
        else:
            groups[best_index][1].append(raw)

    mapping: dict[str, str] = {}
    for _rep, members in groups:
        chosen = max(members, key=len)
        for member in members:
            mapping[member] = chosen
    return mapping
