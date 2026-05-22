"""Step 4 — entity resolution and normalization.

Collapses differently-spelled references to the same part / model / buyer /
supplier into a single canonical id. Precision is prioritized: ambiguous
matches are flagged ``needs_review=True`` rather than merged blindly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd
from rapidfuzz import fuzz, process

from ontology import axioms
from src.utils.logging import get_logger

log = get_logger(__name__)

# Supplier fuzzy-match threshold. Below this, a candidate is not auto-merged.
SUPPLIER_MATCH_THRESHOLD = 90.0

_MODEL_CODE_SPLIT = re.compile(
    r"^(?P<name>[A-Z]{2,5}\d{4,5}[A-Z]?)\.(?P<suffix>[A-Z0-9]+)$"
)


@dataclass
class ModelParts:
    """Decomposed model code."""

    model_name: str
    grade_suffix: str | None
    region: str | None


def resolve_parts(raw_part_nos: list[str]) -> pd.DataFrame:
    """Normalize and dedup a list of raw part numbers.

    Args:
        raw_part_nos: Raw part-number strings from any source form.

    Returns:
        A DataFrame with columns ``canonical_id, raw_value, alias,
        confidence, needs_review, invalid``.
    """
    groups: dict[str, list[str]] = {}
    for raw in raw_part_nos:
        if raw is None or not str(raw).strip():
            continue
        canonical = axioms.normalize_part_no(str(raw))
        groups.setdefault(canonical, [])
        if str(raw) not in groups[canonical]:
            groups[canonical].append(str(raw))

    rows: list[dict[str, object]] = []
    for canonical, aliases in groups.items():
        invalid = not axioms.validate_part_no(canonical)
        rows.append(
            {
                "canonical_id": canonical,
                "raw_value": aliases[0],
                "alias": aliases,
                "confidence": 0.7 if invalid else 1.0,
                "needs_review": invalid,
                "invalid": invalid,
            }
        )
    log.info("resolve.parts", input=len(raw_part_nos), canonical=len(rows))
    return pd.DataFrame(rows)


def parse_model_code(model_code: str) -> ModelParts:
    """Decompose a model code into name / grade-suffix / region.

    Example: ``"WSED7667M.ABMQEUR"`` -> name ``WSED7667M``, suffix ``ABM``,
    region ``EUR``.

    Args:
        model_code: The full model code.

    Returns:
        A :class:`ModelParts`.
    """
    m = _MODEL_CODE_SPLIT.match(model_code.strip().upper())
    if m is None:
        return ModelParts(model_name=model_code.strip(), grade_suffix=None, region=None)
    suffix = m.group("suffix")
    region = None
    for known in ("EUR", "EUE", "EAP", "SJ", "SA"):
        if suffix.endswith(known):
            region = known
            break
    grade_suffix = suffix[:3] if len(suffix) >= 3 else suffix
    return ModelParts(model_name=m.group("name"), grade_suffix=grade_suffix, region=region)


def resolve_models(raw_model_codes: list[str]) -> pd.DataFrame:
    """Normalize and dedup model codes.

    Args:
        raw_model_codes: Raw model-code strings.

    Returns:
        A DataFrame with canonical model rows plus parsed name/grade/region.
    """
    groups: dict[str, list[str]] = {}
    for raw in raw_model_codes:
        if raw is None or not str(raw).strip():
            continue
        canonical = str(raw).strip().upper()
        groups.setdefault(canonical, [])
        if str(raw) not in groups[canonical]:
            groups[canonical].append(str(raw))

    rows: list[dict[str, object]] = []
    for canonical, aliases in groups.items():
        parts = parse_model_code(canonical)
        invalid = not axioms.validate_model_code(canonical)
        rows.append(
            {
                "canonical_id": canonical,
                "raw_value": aliases[0],
                "alias": aliases,
                "model_name": parts.model_name,
                "grade_suffix": parts.grade_suffix,
                "region": parts.region,
                "confidence": 0.7 if invalid else 1.0,
                "needs_review": invalid,
                "invalid": invalid,
            }
        )
    log.info("resolve.models", input=len(raw_model_codes), canonical=len(rows))
    return pd.DataFrame(rows)


def resolve_buyers(raw_buyer_codes: list[str]) -> pd.DataFrame:
    """Map buyer codes to canonical region names.

    Args:
        raw_buyer_codes: Raw buyer-code strings (e.g. ``"LGEUR"``).

    Returns:
        A DataFrame of canonical buyer rows.
    """
    seen: dict[str, list[str]] = {}
    for raw in raw_buyer_codes:
        if raw is None or not str(raw).strip():
            continue
        code = str(raw).strip().upper()
        seen.setdefault(code, [])
        if str(raw) not in seen[code]:
            seen[code].append(str(raw))

    rows: list[dict[str, object]] = []
    for code, aliases in seen.items():
        region = axioms.BUYER_REGIONS.get(code)
        rows.append(
            {
                "canonical_id": code,
                "raw_value": aliases[0],
                "alias": aliases,
                "region": region,
                "confidence": 1.0 if region else 0.6,
                "needs_review": region is None,
                "invalid": region is None,
            }
        )
    log.info("resolve.buyers", input=len(raw_buyer_codes), canonical=len(rows))
    return pd.DataFrame(rows)


def resolve_suppliers(raw_suppliers: list[str]) -> pd.DataFrame:
    """Dedup supplier names via fuzzy matching, precision-first.

    The first occurrence of a name family becomes the canonical id. Candidates
    above :data:`SUPPLIER_MATCH_THRESHOLD` are merged; near-threshold matches
    are flagged ``needs_review=True``.

    Args:
        raw_suppliers: Raw supplier-name strings.

    Returns:
        A DataFrame of canonical supplier rows.
    """
    cleaned = [str(s).strip() for s in raw_suppliers if s is not None and str(s).strip()]
    canonical_names: list[str] = []
    rows: list[dict[str, object]] = []

    for name in cleaned:
        match = process.extractOne(
            name, canonical_names, scorer=fuzz.token_sort_ratio
        )
        if match is not None and match[1] >= SUPPLIER_MATCH_THRESHOLD:
            canonical = match[0]
            needs_review = match[1] < 97.0
            confidence = match[1] / 100.0
        else:
            canonical = name
            canonical_names.append(name)
            needs_review = False
            confidence = 1.0
        rows.append(
            {
                "canonical_id": canonical,
                "raw_value": name,
                "alias": [name],
                "confidence": confidence,
                "needs_review": needs_review,
                "invalid": False,
            }
        )
    df = pd.DataFrame(rows)
    log.info("resolve.suppliers", input=len(cleaned), canonical=len(canonical_names))
    return df


def summarize(df: pd.DataFrame, entity: str) -> dict[str, object]:
    """Build a small stats dict for a resolved entity table.

    Args:
        df: A resolved entity DataFrame.
        entity: Entity name for the report.

    Returns:
        A dict of summary statistics.
    """
    if df.empty:
        return {"entity": entity, "rows": 0}
    return {
        "entity": entity,
        "rows": len(df),
        "invalid_ratio": round(float(df["invalid"].mean()), 4),
        "needs_review_ratio": round(float(df["needs_review"].mean()), 4),
    }
