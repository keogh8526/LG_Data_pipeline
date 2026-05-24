"""Step 2 — answer schema (96col-based).

The 96col form is the *de facto* answer schema for the MVP: it is the form
currently in operation, so it has the most real filled-in data. The v1.2 form
is treated as a future superset, not the current truth (see DECISIONS D-007).

``ChangeEventRow`` is the top-level entity — one row of a normalized 96col
master. Axiom enforcement is wired through field validators in
``src.ontology.axioms``.
"""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from src.ontology import axioms
from src.utils.paths import SCHEMA_JSON_PATH


# --- 96col group sub-models ----------------------------------------------


class CommonFields(BaseModel):
    """공통 그룹 — 모델/개발 메타데이터."""

    base_model: str | None = None
    new_model: str | None = None
    grade: str | None = None
    buyer: str | None = None
    production_date: str | None = None


class DRBFMFields(BaseModel):
    """DRBFM 그룹 — 변경점 우려 분석."""

    concern: str | None = None
    countermeasure: str | None = None


class PartCertFields(BaseModel):
    """부품 인증/등급 그룹."""

    grade_decision: str | None = None
    dev_status: str | None = None


class HSMSFields(BaseModel):
    """HSMS 그룹 — 유해물질 관리."""

    hazard_status: str | None = None


class MoldFields(BaseModel):
    """금형/사출 그룹."""

    mold_no: str | None = None
    cavity: int | None = None


# --- Top-level entity -----------------------------------------------------


class ChangeEventRow(BaseModel):
    """한 행의 정규화된 96col 마스터 — 부품 변경 이벤트."""

    # Fixed features (10).
    base_part_no: str
    new_part_no: str | None = None
    part_name: str
    bom_level: int = Field(ge=0, le=10)
    part_type: str
    change_type: str
    change_point: str
    change_reason: str | None = None
    qty: float | None = None
    model_code: str

    # 96col groups.
    common: CommonFields = Field(default_factory=CommonFields)
    drbfm: DRBFMFields = Field(default_factory=DRBFMFields)
    part_cert: PartCertFields = Field(default_factory=PartCertFields)
    hsms: HSMSFields = Field(default_factory=HSMSFields)
    mold: MoldFields = Field(default_factory=MoldFields)

    # Provenance / batch metadata.
    source_file: str
    form_version: str
    extracted_at: datetime | None = None
    run_id: str

    @field_validator("base_part_no", "new_part_no", mode="before")
    @classmethod
    def _normalize_part_no(cls, value: object) -> object:
        # Normalize only — invalid part numbers are recorded as data errors
        # downstream (quarantine), never raised here.
        if isinstance(value, str) and value.strip():
            return axioms.normalize_part_no(value)
        return value

    @field_validator("change_type", mode="before")
    @classmethod
    def _normalize_change_type(cls, value: object) -> str:
        canonical = axioms.normalize_change_type(str(value))
        if canonical is None:
            raise ValueError(f"unknown change_type: {value!r}")
        return canonical


def export_schema_json(path: object = SCHEMA_JSON_PATH) -> None:
    """Export the JSON Schema for :class:`ChangeEventRow`.

    Args:
        path: Output path for ``schema.json``.
    """
    schema = ChangeEventRow.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    path.write_text(  # type: ignore[attr-defined]
        json.dumps(schema, indent=2, ensure_ascii=False), encoding="utf-8"
    )
