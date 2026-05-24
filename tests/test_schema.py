"""Tests for the Step 2 answer schema (96col) and axioms."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.ontology import axioms
from src.ontology.schema import ChangeEventRow, export_schema_json


def _valid_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "base_part_no": "AB1234567",
        "new_part_no": "AB1234568",
        "part_name": "Bracket",
        "bom_level": 2,
        "part_type": "단품",
        "change_type": "Change",
        "change_point": "내열 보강",
        "model_code": "WSED7667M.ABMQEUR",
        "source_file": "sample.xlsx",
        "form_version": "v96col",
        "run_id": "run_test",
    }
    base.update(overrides)
    return base


def test_valid_row_instantiates() -> None:
    row = ChangeEventRow(**_valid_kwargs())
    assert row.base_part_no == "AB1234567"
    assert row.common.grade is None  # group sub-models default


def test_change_type_alias_normalized() -> None:
    assert ChangeEventRow(**_valid_kwargs(change_type="K")).change_type == "Carry-over"
    assert ChangeEventRow(**_valid_kwargs(change_type="신규")).change_type == "New"


def test_unknown_change_type_rejected() -> None:
    with pytest.raises(ValidationError):
        ChangeEventRow(**_valid_kwargs(change_type="Foo"))


def test_part_no_normalized_not_rejected() -> None:
    # Messy input is normalized; invalid numbers are NOT raised here.
    row = ChangeEventRow(**_valid_kwargs(base_part_no="ab-123 4567"))
    assert row.base_part_no == "AB1234567"


def test_bom_level_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        ChangeEventRow(**_valid_kwargs(bom_level=99))


def test_axiom_part_no() -> None:
    assert axioms.validate_part_no("AB1234567")
    assert not axioms.validate_part_no("123")


def test_axiom_change_type_alias() -> None:
    assert axioms.normalize_change_type("K") == "Carry-over"
    assert axioms.normalize_change_type("nonsense") is None


def test_axiom_grade_alias() -> None:
    assert axioms.normalize_grade("Best1") == "Best-1"


def test_axiom_model_code_and_bom_level() -> None:
    assert axioms.validate_model_code("WSED7667M.ABMQEUR")
    assert axioms.validate_bom_level(5)
    assert not axioms.validate_bom_level(99)


def test_schema_json_export(tmp_path: Path) -> None:
    out = tmp_path / "schema.json"
    export_schema_json(out)
    schema = json.loads(out.read_text(encoding="utf-8"))
    assert schema["title"] == "ChangeEventRow"
    assert "$schema" in schema
