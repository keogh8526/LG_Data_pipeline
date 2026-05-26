"""v2.0 Step 5 — SQLAlchemy ORM (preprocessing_v2.md §9).

Core 13 + JSONB payload + Multi-Vector. 벡터 컬럼(``vector(1024)``)과 인덱스는
``src/db/schema.sql``에서 별도 적용 — ORM은 portable하게 유지해 SQLite 단위
테스트와 호환된다.

테이블:
  parts                — 부품 마스터
  models               — 모델 메타
  bom_edges            — BOM 트리
  change_events        — 메인 RAG 검색 대상 (Core 13 + JSONB payload)
  test_plans           — 부품인정시험
  hsms_records         — 친환경
  preprocessing_runs   — 매 run 기록 (dry_run / committed / rolled_back)
  form_versions        — 양식 진화 이력
  needs_review_queue   — 사람 검토 큐
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CHAR,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    TypeDecorator,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UUIDType(TypeDecorator):
    """Postgres에선 native UUID, SQLite/기타에선 CHAR(36)로 자동 변환.

    SQLAlchemy의 .with_variant()는 컬럼 타입은 갈아끼우지만 bind/result에서
    uuid.UUID 객체 ↔ 문자열 변환을 책임지지 않는다 — TypeDecorator로 해결.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError):
            return value


def _uuid_pk():
    return mapped_column(UUIDType(), primary_key=True, default=uuid.uuid4)


def _uuid_fk(target: str, nullable: bool = True):
    return mapped_column(UUIDType(), ForeignKey(target), nullable=nullable)


def _uuid_col(nullable: bool = True):
    return mapped_column(UUIDType(), nullable=nullable, index=True)


# Portable JSONB: Postgres = JSONB, SQLite = JSON.
_JSONB = JSONB().with_variant(JSON, "sqlite")


# Portable ARRAY[TEXT]: Postgres = ARRAY(TEXT), SQLite = JSON list.
_TEXT_ARRAY = ARRAY(Text).with_variant(JSON, "sqlite")


class Base(DeclarativeBase):
    """모든 테이블의 declarative base."""


# ── Parts ────────────────────────────────────────────────────────────


class Part(Base):
    """부품 마스터. Base/New 구분 없이 모든 P/No 한 행씩."""

    __tablename__ = "parts"

    part_no: Mapped[str] = mapped_column(String(40), primary_key=True)
    part_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    technical_spec: Mapped[str | None] = mapped_column(Text, default=None)
    bom_level: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    part_type: Mapped[str | None] = mapped_column(String(50), default=None)
    maker: Mapped[str | None] = mapped_column(String(100), default=None)
    standard: Mapped[str | None] = mapped_column(String(100), default=None)
    uom: Mapped[str | None] = mapped_column(String(20), default=None)
    aliases: Mapped[list | None] = mapped_column(_TEXT_ARRAY, default=None)
    source_file: Mapped[str | None] = mapped_column(Text, default=None)
    first_seen_run_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


# ── Models ───────────────────────────────────────────────────────────


class Model(Base):
    """모델 메타."""

    __tablename__ = "models"

    model_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    model_name: Mapped[str | None] = mapped_column(String(30), default=None)
    grade: Mapped[str | None] = mapped_column(String(20), default=None)
    region: Mapped[str | None] = mapped_column(String(5), default=None)
    buyer_code: Mapped[str | None] = mapped_column(String(10), default=None)
    production_date: Mapped[str | None] = mapped_column(Date, default=None)
    size_inch: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    brand: Mapped[str | None] = mapped_column(String(30), default=None)
    run_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)


# ── BOM Edges ────────────────────────────────────────────────────────


class BomEdge(Base):
    """BOM 트리 (recursive CTE로 multi-hop 추적).

    B-2: parent/child → parts FK, model_code → models FK 명시.
    model_code가 적재 시점에 없을 수 있어 load.py가 'UNKNOWN' Model upsert 보장.
    """

    __tablename__ = "bom_edges"

    model_code: Mapped[str] = mapped_column(
        String(50),
        ForeignKey("models.model_code"),
        primary_key=True,
    )
    parent_part_no: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("parts.part_no"),
        primary_key=True,
    )
    child_part_no: Mapped[str] = mapped_column(
        String(40),
        ForeignKey("parts.part_no"),
        primary_key=True,
    )
    qty: Mapped[float | None] = mapped_column(Numeric(10, 3), default=None)
    bom_level: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    change_in: Mapped[str | None] = mapped_column(Date, default=None)
    change_out: Mapped[str | None] = mapped_column(Date, default=None)
    run_id: Mapped[str] = mapped_column(String(36), index=True)


# ── Change Events (메인) ────────────────────────────────────────────


class ChangeEvent(Base):
    """메인 RAG 검색 대상. Core 13 + JSONB payload + Multi-Vector 5개.

    벡터 컬럼(``narrative_emb`` 등)은 ORM에 정의하지 않음 — schema.sql에서
    ``ALTER TABLE ... ADD COLUMN vector(1024)``로 추가. SQLite 테스트는
    벡터 컬럼을 무시한다.
    """

    __tablename__ = "change_events"

    event_id: Mapped[uuid.UUID] = _uuid_pk()

    # ── Core 13 (양식 공통, 타입 강제, b-tree 인덱스) ──
    part_no: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("parts.part_no"), index=True, default=None
    )
    part_name: Mapped[str | None] = mapped_column(Text, default=None)
    base_part_no: Mapped[str | None] = mapped_column(
        String(40), ForeignKey("parts.part_no"), index=True, default=None
    )
    base_model_code: Mapped[str | None] = mapped_column(String(50), default=None)
    new_model_code: Mapped[str | None] = mapped_column(
        String(50), ForeignKey("models.model_code"), index=True, default=None
    )
    grade: Mapped[str | None] = mapped_column(String(20), index=True, default=None)
    region: Mapped[str | None] = mapped_column(String(5), index=True, default=None)
    change_type: Mapped[str | None] = mapped_column(String(20), index=True, default=None)
    event_stage: Mapped[str | None] = mapped_column(String(5), index=True, default=None)
    change_point: Mapped[str | None] = mapped_column(Text, default=None)
    change_reason: Mapped[str | None] = mapped_column(Text, default=None)
    bom_level: Mapped[int | None] = mapped_column(SmallInteger, default=None)
    part_type: Mapped[str | None] = mapped_column(String(50), default=None)

    # ── extra_fields (D-011: Core 13 매핑 안 된 컬럼만 JSONB로 보존) ──
    extra_fields: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)
    form_version: Mapped[str] = mapped_column(String(30), index=True, nullable=False)
    source_file: Mapped[str] = mapped_column(Text, nullable=False)
    source_sheet: Mapped[str] = mapped_column(Text, nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Narrative (Multi-Vector 컬럼은 schema.sql에서 추가) ──
    narrative_text: Mapped[str | None] = mapped_column(Text, default=None)

    # ── 운영 메타 ──
    run_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(3, 2), default=1.0)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# D-011: TestPlan / HsmsRecord 도메인 모델은 v2.0 간소화로 제거.
# 현 BOM Agent 시나리오에서 도메인 부속 테이블(부품인정시험 / HSMS)은 활용 안 됨.
# 필요해지면 ORM 재추가 + schema.sql ALTER 필요.


# ── Preprocessing Runs ──────────────────────────────────────────────


class PreprocessingRun(Base):
    """run 기록. run_id는 모든 적재 테이블의 batch handle."""

    __tablename__ = "preprocessing_runs"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    status: Mapped[str | None] = mapped_column(String(20), default=None)
    rows_inserted: Mapped[dict | None] = mapped_column(_JSONB, default=None)
    config_snapshot: Mapped[dict | None] = mapped_column(_JSONB, default=None)
    validation_report: Mapped[dict | None] = mapped_column(_JSONB, default=None)
    golden_diff_report: Mapped[dict | None] = mapped_column(_JSONB, default=None)
    files_processed: Mapped[dict | None] = mapped_column(_JSONB, default=None)
    operator: Mapped[str | None] = mapped_column(Text, default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)


# D-011 Phase C: FormVersion + NeedsReview 테이블 삭제.
#   - FormVersion: 양식 진화 추적은 form_signatures.yaml 단독으로 충분.
#   - NeedsReview: 3-band ER 제거되며 사람 검토 큐도 미사용.


__all__ = [
    "Base",
    "BomEdge",
    "ChangeEvent",
    "Model",
    "Part",
    "PreprocessingRun",
]
# 5 테이블: parts / models / bom_edges / change_events / preprocessing_runs.
