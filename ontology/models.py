"""Pydantic v2 models mirroring v1_2_schema.json — the answer ontology.

The ``ChangeEvent`` model is the top-level entity. Axiom enforcement is wired
through field validators in ``ontology.axioms``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from ontology import axioms


class CommonGroup(BaseModel):
    """공통 그룹 — 모델 메타데이터."""

    base_model: str | None = None
    new_model: str | None = None
    grade: str | None = None
    buyer: str | None = None
    production_date: str | None = None


class DrbfmGroup(BaseModel):
    """DRBFM 그룹."""

    concern: str | None = None


class PartsGradeGroup(BaseModel):
    """부품등급심의 그룹."""

    grade_decision: str | None = None


class PartsDevGroup(BaseModel):
    """부품개발완료 그룹."""

    dev_status: str | None = None


class FmeaGroup(BaseModel):
    """FMEA 그룹."""

    failure_mode: str | None = None


class HsmsGroup(BaseModel):
    """HSMS 그룹 (유해물질)."""

    hazard_status: str | None = None


class FormVersion(BaseModel):
    """통합 v1.2 History 시트 — 양식 버전 이력 entity."""

    version: str
    released_at: str | None = None
    change_summary: str | None = None
    updated_by: str | None = None


class Part(BaseModel):
    """부품 entity (정규화 후)."""

    part_no: str
    part_name: str | None = None
    bom_level: int | None = None
    part_type: str | None = None


class Model(BaseModel):
    """모델 entity."""

    model_code: str
    grade: str | None = None
    region: str | None = None
    buyer_code: str | None = None
    production_date: str | None = None


class TestPlan(BaseModel):
    """시험 계획 sub-entity."""

    plan_id: str
    test_item: str | None = None
    responsible: str | None = None


class ChangeEvent(BaseModel):
    """최상위 entity — 한 건의 부품 변경 이벤트."""

    event_id: str | None = None
    base_part_no: str | None = None
    new_part_no: str
    part_name: str | None = None
    bom_level: int | None = Field(default=None, ge=0)
    part_type: str | None = None
    change_type: str
    change_point: str | None = None
    change_reason: str | None = None
    qty: float | None = None
    model_code: str

    source_file: str | None = None
    form_version: str | None = None
    extracted_at: datetime | None = None

    common: CommonGroup = Field(default_factory=CommonGroup)
    drbfm: DrbfmGroup = Field(default_factory=DrbfmGroup)
    parts_grade: PartsGradeGroup = Field(default_factory=PartsGradeGroup)
    parts_dev: PartsDevGroup = Field(default_factory=PartsDevGroup)
    fmea: FmeaGroup = Field(default_factory=FmeaGroup)
    hsms: HsmsGroup = Field(default_factory=HsmsGroup)

    @field_validator("change_type")
    @classmethod
    def _check_change_type(cls, value: str) -> str:
        if not axioms.validate_change_type(value):
            raise ValueError(f"unknown change_type: {value!r}")
        return value

    @field_validator("new_part_no")
    @classmethod
    def _check_new_part_no(cls, value: str) -> str:
        # Normalize but do not reject — invalid part numbers are recorded as
        # data errors downstream, not raised here.
        return axioms.normalize_part_no(value)
