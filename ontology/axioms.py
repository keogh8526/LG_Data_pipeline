"""Deterministic domain axioms for the LG parts pipeline.

These rules are the ground truth for validation. They never call an LLM.
Part-number / model-code patterns are derived from the project spec and may
need refinement once real data is available (see ``# TODO(real-data)``).
"""

from __future__ import annotations

import re

# --- Patterns -------------------------------------------------------------

# Part number: 2-3 leading letters + 7-8 digits (10-char coding rule).
PART_NO_PATTERN = re.compile(r"^[A-Z]{2,3}\d{7,8}$")

# Model code: e.g. "WSED7667M.ABMQEUR".
MODEL_CODE_PATTERN = re.compile(r"^[A-Z]{2,5}\d{4,5}[A-Z]?\.[A-Z0-9]+$")

# --- Controlled vocabularies ---------------------------------------------

GRADE_VALUES: frozenset[str] = frozenset(
    {
        "Best-1",
        "Best-2",
        "Better-1",
        "Better-2",
        "Good-1",
        "Good-2",
        "Good-1 BK",
        "Good-2 BK",
    }
)

CHANGE_TYPES: frozenset[str] = frozenset({"New", "Change", "Carry-over"})

EVENT_STAGES: tuple[str, ...] = ("CP", "PP", "DV", "PV", "PQ")

# Buyer code -> human-readable region.
BUYER_REGIONS: dict[str, str] = {
    "LGEUR": "Europe",
    "LGEUE": "East Europe",
    "LGEAP": "Asia Pacific",
    "LGESJ": "Sao Joao",
    "LGESA": "South America",
}

# --- Validators -----------------------------------------------------------


def normalize_part_no(value: str) -> str:
    """Normalize a raw part number: strip, uppercase, drop spaces/hyphens.

    Args:
        value: Raw part-number string.

    Returns:
        The normalized part number.
    """
    return re.sub(r"[\s\-]", "", value.strip()).upper()


def validate_part_no(value: str) -> bool:
    """Return True if ``value`` matches the part-number coding rule.

    Args:
        value: Part-number string (already normalized or not).
    """
    return bool(PART_NO_PATTERN.match(normalize_part_no(value)))


def validate_model_code(value: str) -> bool:
    """Return True if ``value`` matches the model-code pattern.

    Args:
        value: Model-code string.
    """
    return bool(MODEL_CODE_PATTERN.match(value.strip().upper()))


def validate_grade(value: str) -> bool:
    """Return True if ``value`` is a known grade.

    Args:
        value: Grade label.
    """
    return value.strip() in GRADE_VALUES


def validate_change_type(value: str) -> bool:
    """Return True if ``value`` is a known change type.

    Args:
        value: Change-type label.
    """
    return value.strip() in CHANGE_TYPES


def validate_event_stage(value: str) -> bool:
    """Return True if ``value`` is a known event stage.

    Args:
        value: Event-stage label.
    """
    return value.strip().upper() in EVENT_STAGES
