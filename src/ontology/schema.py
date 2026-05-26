"""v2.0 Step 2 — Core 13필드 + JSONB Payload + Multi-Vector 스키마.

preprocessing_v2.md §4-2 그대로 구현. "Schema-on-Discovery" 원칙:
- Core 13필드만 타입 강제 (양식 공통)
- 나머지 양식 원본은 payload(JSONB)로 100% 보존
- 자유텍스트는 semantic_text에 raw로 저장 → narrativize/embed에서 사용
"""

from __future__ import annotations

import json
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.ontology import axioms
from src.utils.paths import SCHEMA_JSON_PATH


# --- Enums ----------------------------------------------------------------


class Grade(str, Enum):
    """부품 등급 (axioms.yaml grade.allowed와 일치)."""

    BEST_1 = "Best-1"
    BEST_2 = "Best-2"
    BETTER_1 = "Better-1"
    BETTER_2 = "Better-2"
    GOOD_1 = "Good-1"
    GOOD_2 = "Good-2"
    GOOD_1_BK = "Good-1 BK"
    GOOD_2_BK = "Good-2 BK"
    GOOD_1_STS = "Good-1 STS"
    GOOD_2_STS = "Good-2 STS"
    UNKNOWN = "unknown"


class Region(str, Enum):
    """지역 코드 (axioms.yaml region.allowed와 일치)."""

    EUR = "EUR"
    EUE = "EUE"
    EAP = "EAP"
    SJ = "SJ"
    SA = "SA"
    AU = "AU"
    SG = "SG"
    KR = "KR"
    US = "US"
    UNKNOWN = "unknown"


class ChangeType(str, Enum):
    """변경 유형."""

    NEW = "New"
    CHANGE = "Change"
    CARRY_OVER = "Carry-over"


class EventStage(str, Enum):
    """현 개발 단계."""

    CP = "CP"
    PP = "PP"
    DV = "DV"
    PV = "PV"
    PQ = "PQ"
    MP = "MP"
    PRE_MP = "PreMP"


class PartType(str, Enum):
    """부품 유형."""

    INJECTION = "사출"
    ASSY = "Assy"
    ELECTRONIC = "전장"
    SINGLE = "단품"
    OTHER = "기타"


# --- Core 13필드 ---------------------------------------------------------


class CoreFields(BaseModel):
    """모든 양식 공통 추출 대상. preprocessing_v2.md §4-1.

    13개 필드 = 명시적 필수 10개 + 옵셔널이지만 자주 등장 3개(base_part_no,
    bom_level, part_type, region까지 사실상 14개지만 문서 관례상 "Core 13").
    """

    model_config = ConfigDict(use_enum_values=True, validate_assignment=False)

    # 명시적 필수
    part_no: str = Field(..., max_length=15, description="새 부품번호 (변경 후)")
    part_name: str = Field(..., description="부품명/품명/형상명")
    new_model_code: str = Field(..., max_length=50, description="새 모델 코드")
    grade: Grade | str = Field(..., description="부품 등급")
    change_type: ChangeType | str = Field(..., description="변경 유형")

    # 옵셔널 but 자주 등장
    base_part_no: Optional[str] = Field(None, max_length=15)
    base_model_code: Optional[str] = Field(None, max_length=50)
    region: Optional[Region | str] = None
    event_stage: Optional[EventStage | str] = None
    change_point: Optional[str] = None
    change_reason: Optional[str] = None
    bom_level: Optional[int] = Field(None, ge=0, le=10)
    part_type: Optional[PartType | str] = None

    @field_validator("part_no", "base_part_no", mode="before")
    @classmethod
    def _normalize_part_no(cls, value: object) -> object:
        # 정규화만; axiom 위반은 quarantine 단계에서 거름 (raise 금지).
        if value is None:
            return None
        s = str(value).strip()
        if not s:
            return None
        return axioms.normalize_part_no(s)

    @field_validator("change_type", mode="before")
    @classmethod
    def _normalize_change_type(cls, value: object) -> object:
        if value is None:
            return value
        canonical = axioms.normalize_change_type(str(value))
        if canonical is None:
            raise ValueError(f"unknown change_type: {value!r}")
        return canonical

    @field_validator("grade", mode="before")
    @classmethod
    def _normalize_grade(cls, value: object) -> object:
        if value is None or str(value).strip() == "":
            return Grade.UNKNOWN.value
        canonical = axioms.normalize_grade(str(value))
        return canonical or Grade.UNKNOWN.value

    @field_validator("region", mode="before")
    @classmethod
    def _normalize_region(cls, value: object) -> object:
        if value is None or str(value).strip() == "":
            return None
        upper = str(value).strip().upper()
        if upper in {r.value for r in Region}:
            return upper
        return Region.UNKNOWN.value

    @field_validator("part_type", mode="before")
    @classmethod
    def _normalize_part_type(cls, value: object) -> object:
        if value is None or str(value).strip() == "":
            return None
        return axioms.normalize_part_type(str(value)) or PartType.OTHER.value


# --- ChangeEvent (한 행 = 한 이벤트) -------------------------------------


class ChangeEvent(BaseModel):
    """한 행 = 하나의 ChangeEvent. Core + extra_fields + Narrative.

    D-011 (B): payload(100% 보존) → extra_fields(Core 13 매핑 안 된 컬럼만)로 축소.
    semantic_text는 narrative_text 생성 후 사용 안 되므로 제거.

    - core: Core 13필드 (타입 강제, PG 컬럼)
    - extra_fields: Core 매핑되지 않은 원본 컬럼 (JSONB, 답변 시 LLM 컨텍스트로 활용 가능)
    - narrative_text: Step 5 narrativize 결과 (200~600 토큰)
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    core: CoreFields
    extra_fields: dict[str, Any] = Field(default_factory=dict)
    narrative_text: Optional[str] = None

    # Provenance
    form_version: str = Field(..., description="예: 변경부품_list_96, BOM_ag_grid_36")
    source_file: str
    source_sheet: str
    source_row: int

    # Run / ER 메타
    run_id: str
    confidence: float = 1.0
    needs_review: bool = False
    extracted_at: Optional[datetime] = None


# --- Part / Model / BOMEdge ---------------------------------------------


class Part(BaseModel):
    """부품 마스터 한 행."""

    part_no: str = Field(..., max_length=15)
    part_name: str
    description: Optional[str] = None
    technical_spec: Optional[str] = None
    bom_level: Optional[int] = Field(None, ge=0, le=10)
    part_type: Optional[str] = None
    maker: Optional[str] = None
    standard: Optional[str] = None
    uom: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    run_id: Optional[str] = None


class Model(BaseModel):
    """모델 메타."""

    model_code: str = Field(..., max_length=50)
    model_name: Optional[str] = Field(None, max_length=30)
    grade: Optional[str] = None
    region: Optional[str] = None
    buyer_code: Optional[str] = None
    production_date: Optional[str] = None
    size_inch: Optional[int] = None
    brand: Optional[str] = None
    run_id: Optional[str] = None


class BOMEdge(BaseModel):
    """BOM 부모-자식 관계 한 행."""

    model_code: str
    parent_part_no: str
    child_part_no: str
    qty: Optional[float] = None
    bom_level: Optional[int] = None
    change_in: Optional[str] = None
    change_out: Optional[str] = None
    run_id: str


# --- JSON Schema export -------------------------------------------------


def export_schema_json(path: Optional[object] = None) -> None:
    """ChangeEvent + 보조 모델의 JSON Schema(Draft 2020-12) export.

    Args:
        path: 출력 파일 경로. None이면 ``SCHEMA_JSON_PATH`` 사용.
    """
    out_path = path if path is not None else SCHEMA_JSON_PATH
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://lg-bom/v2.0/schema.json",
        "title": "LG BOM v2.0 ontology",
        "definitions": {
            "CoreFields": CoreFields.model_json_schema(),
            "ChangeEvent": ChangeEvent.model_json_schema(),
            "Part": Part.model_json_schema(),
            "Model": Model.model_json_schema(),
            "BOMEdge": BOMEdge.model_json_schema(),
        },
    }
    out_path.write_text(  # type: ignore[attr-defined]
        json.dumps(schema, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


__all__ = [
    "BOMEdge",
    "ChangeEvent",
    "ChangeType",
    "CoreFields",
    "EventStage",
    "Grade",
    "Model",
    "Part",
    "PartType",
    "Region",
    "export_schema_json",
]
