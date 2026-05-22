"""Tests for the ontology: axioms, Pydantic models, JSON Schema."""

from __future__ import annotations

import json

import jsonschema
import pytest

from ontology import axioms
from ontology.models import ChangeEvent
from src.utils.paths import V1_2_SCHEMA_PATH

VALID_PART_NOS = ["AB1234567", "ABC12345678", "ab-123 4567", "XY9999999"]
INVALID_PART_NOS = ["12345678", "A1234567", "ABCD1234567", "AB12345", ""]


@pytest.mark.parametrize("value", VALID_PART_NOS)
def test_valid_part_no(value: str) -> None:
    assert axioms.validate_part_no(value)


@pytest.mark.parametrize("value", INVALID_PART_NOS)
def test_invalid_part_no(value: str) -> None:
    assert not axioms.validate_part_no(value)


def test_model_code_validation() -> None:
    assert axioms.validate_model_code("WSED7667M.ABMQEUR")
    assert not axioms.validate_model_code("not-a-model")


def test_change_type_validation() -> None:
    assert axioms.validate_change_type("Change")
    assert not axioms.validate_change_type("Modified")


def test_json_schema_self_validates() -> None:
    schema = json.loads(V1_2_SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)


def test_change_event_instantiates() -> None:
    event = ChangeEvent(
        new_part_no="ab1234568",
        base_part_no="AB1234567",
        change_type="Change",
        change_point="내열 보강",
        bom_level=2,
        part_type="단품",
        model_code="WSED7667M.ABMQEUR",
    )
    assert event.new_part_no == "AB1234568"
    assert event.common.base_model is None


def test_change_event_rejects_unknown_change_type() -> None:
    with pytest.raises(ValueError, match="change_type"):
        ChangeEvent(
            new_part_no="AB1234568",
            change_type="Bogus",
            model_code="WSED7667M.ABMQEUR",
        )
