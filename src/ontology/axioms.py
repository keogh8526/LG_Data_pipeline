"""Step 2 — deterministic domain axioms (config-driven).

Validation rules are loaded from ``config/axioms.yaml`` so they can be tuned
without touching code. No function here ever calls an LLM.
"""

from __future__ import annotations

import re
from functools import lru_cache

import yaml

from src.utils.paths import AXIOMS_PATH


@lru_cache(maxsize=1)
def _config() -> dict[str, object]:
    """Load and cache the axioms config."""
    return yaml.safe_load(AXIOMS_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=8)
def _pattern(key: str) -> re.Pattern[str]:
    """Compile and cache a regex pattern from the config."""
    section: dict[str, object] = _config()[key]  # type: ignore[assignment]
    return re.compile(str(section["pattern"]))


# --- Normalizers ----------------------------------------------------------


def normalize_part_no(value: str) -> str:
    """Normalize a raw part number: strip, uppercase, drop spaces/hyphens.

    Args:
        value: Raw part-number string.

    Returns:
        The normalized part number.
    """
    return re.sub(r"[\s\-_]", "", value.strip()).upper()


def normalize_change_type(value: str) -> str | None:
    """Resolve a change-type label through the configured aliases.

    Args:
        value: Raw change-type label (may be an alias).

    Returns:
        The canonical change type, or None if unrecognized.
    """
    section: dict[str, object] = _config()["change_type"]  # type: ignore[assignment]
    cleaned = value.strip()
    allowed: list[str] = section["allowed"]  # type: ignore[assignment]
    if cleaned in allowed:
        return cleaned
    aliases: dict[str, str] = section.get("aliases", {})  # type: ignore[assignment]
    return aliases.get(cleaned)


def normalize_grade(value: str) -> str | None:
    """Resolve a grade label through the configured aliases.

    Args:
        value: Raw grade label (may be an alias).

    Returns:
        The canonical grade, or None if unrecognized.
    """
    section: dict[str, object] = _config()["grade"]  # type: ignore[assignment]
    cleaned = value.strip()
    allowed: list[str] = section["allowed"]  # type: ignore[assignment]
    if cleaned in allowed:
        return cleaned
    aliases: dict[str, str] = section.get("aliases", {})  # type: ignore[assignment]
    return aliases.get(cleaned)


# --- Validators -----------------------------------------------------------


def validate_part_no(value: str) -> bool:
    """Return True if ``value`` matches the part-number coding rule.

    Args:
        value: Part-number string (normalized first).
    """
    return bool(_pattern("part_no").match(normalize_part_no(value)))


def validate_model_code(value: str) -> bool:
    """Return True if ``value`` matches the model-code pattern.

    Args:
        value: Model-code string.
    """
    return bool(_pattern("model_code").match(value.strip().upper()))


def validate_change_type(value: str) -> bool:
    """Return True if ``value`` is a known change type or alias.

    Args:
        value: Change-type label.
    """
    return normalize_change_type(value) is not None


def validate_grade(value: str) -> bool:
    """Return True if ``value`` is a known grade or alias.

    Args:
        value: Grade label.
    """
    return normalize_grade(value) is not None


def validate_event_stage(value: str) -> bool:
    """Return True if ``value`` is a known event stage.

    Args:
        value: Event-stage label.
    """
    allowed: list[str] = _config()["event_stage"]["allowed"]  # type: ignore[index]
    return value.strip().upper() in allowed


def validate_bom_level(value: int) -> bool:
    """Return True if ``value`` is within the allowed BOM-level range.

    Args:
        value: BOM tree depth.
    """
    section: dict[str, object] = _config()["bom_level"]  # type: ignore[assignment]
    return int(section["min"]) <= value <= int(section["max"])  # type: ignore[arg-type]
