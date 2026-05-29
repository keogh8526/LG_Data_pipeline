"""D-012 — SQLAlchemy ORM for dev_part_master schema (팀원 ETL_PG 통합).

4 테이블 (이전 9 → 5 → 4로 축소):
  source_files    — 원본 파일 메타 (file_hash 기준 dedup)
  ingestion_log   — 시트별 처리 결과
  form_registry   — 지원 양식 등록
  dev_part_master — 메인 데이터 (Core 13 + extra_fields + narrative + embedding)

벡터 컬럼(``embedding_dense vector(1024)``)은 Postgres 전용 — pgvector 확장.
SQLite 단위 테스트에서는 Vector → JSON으로 fallback.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


# Portable JSONB: Postgres = JSONB, SQLite = JSON.
_JSONB = JSONB().with_variant(JSON, "sqlite")

# SQLite는 BIGINT 컬럼에 AUTOINCREMENT를 적용하지 않음 — INTEGER로 대체해야
# autoincrement 동작.
_BIGINT_PK = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    """모든 테이블의 declarative base."""


# ── source_files ─────────────────────────────────────────────


class SourceFile(Base):
    """원본 엑셀 파일 메타. ``file_hash``로 중복 적재 방지."""

    __tablename__ = "source_files"

    file_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, default=None)
    region: Mapped[str | None] = mapped_column(Text, default=None)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── ingestion_log ────────────────────────────────────────────


class IngestionLog(Base):
    """시트별 처리 결과. status='ok'/'error'/'empty' 등."""

    __tablename__ = "ingestion_log"

    log_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        _BIGINT_PK, ForeignKey("source_files.file_id", ondelete="CASCADE")
    )
    sheet_name: Mapped[str] = mapped_column(Text, nullable=False)
    form_id: Mapped[str] = mapped_column(Text, nullable=False)
    rows_total: Mapped[int | None] = mapped_column(Integer, default=None)
    rows_inserted: Mapped[int | None] = mapped_column(Integer, default=None)
    status: Mapped[str | None] = mapped_column(Text, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    logged_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── form_registry ────────────────────────────────────────────


class FormRegistry(Base):
    """지원 양식 등록. schema_dev_part_master.sql이 seed."""

    __tablename__ = "form_registry"

    form_id: Mapped[str] = mapped_column(Text, primary_key=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)


# ── dev_part_master ──────────────────────────────────────────


class DevPartMaster(Base):
    """메인 테이블 (한 row = 한 부품 변경/신규 이벤트 또는 BOM 부품).

    팀원 ETL_PG 스키마 그대로 + ``extra_fields`` JSONB + RAG용 ``embedding_text`` /
    ``embedding_dense``.

    벡터 컬럼은 Postgres에서만 활성화. SQLite 단위 테스트에선 None.
    """

    __tablename__ = "dev_part_master"

    doc_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    file_id: Mapped[int] = mapped_column(
        _BIGINT_PK, ForeignKey("source_files.file_id", ondelete="CASCADE")
    )
    form_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("form_registry.form_id", ondelete="RESTRICT"), default=None
    )
    sheet_name: Mapped[str | None] = mapped_column(Text, default=None)
    source_row: Mapped[int | None] = mapped_column(Integer, default=None)

    # 팀원 dev_part_master 컬럼
    region: Mapped[str | None] = mapped_column(Text, default=None)
    base_model: Mapped[str | None] = mapped_column(Text, default=None)
    new_model: Mapped[str | None] = mapped_column(Text, default=None)
    event: Mapped[str | None] = mapped_column(Text, default=None)
    bom_level_raw: Mapped[str | None] = mapped_column(Text, default=None)
    bom_depth: Mapped[int | None] = mapped_column(Integer, default=None)
    part_type: Mapped[str | None] = mapped_column(Text, default=None)
    part_no_base: Mapped[str | None] = mapped_column(Text, default=None)
    part_no_new: Mapped[str | None] = mapped_column(Text, default=None)
    part_name: Mapped[str | None] = mapped_column(Text, default=None)
    qty_base: Mapped[float | None] = mapped_column(Numeric, default=None)
    qty_new: Mapped[float | None] = mapped_column(Numeric, default=None)
    change_point_raw: Mapped[str | None] = mapped_column(Text, default=None)
    change_reason_raw: Mapped[str | None] = mapped_column(Text, default=None)
    supplier: Mapped[str | None] = mapped_column(Text, default=None)
    classification: Mapped[str | None] = mapped_column(Text, default=None)

    # 표준 매핑 안 된 컬럼 (grade, event_stage, 양식 잔여 헤더 등)
    extra_fields: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, default=None)

    # RAG 검색용
    embedding_text: Mapped[str | None] = mapped_column(Text, default=None)
    embedding_dense = mapped_column(
        Vector(1024).with_variant(JSON, "sqlite"), nullable=True, default=None
    )

    # Agentic RAG 가산 (2026-05-29): L1 ChangeIntent 결과 캐시 (additive 컬럼).
    change_intent: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ════════════════════════════════════════════════════════════════
# Agentic RAG 가산 테이블 (2026-05-29, migrations/001_agentic_rag_additive.sql)
# 기존 4테이블 파괴적 변경 0 — 보조 테이블만. Postgres에선 migration SQL이,
# SQLite 단위 테스트에선 create_all이 생성. 컬럼은 migration SQL과 1:1 정합.
# ════════════════════════════════════════════════════════════════


class BomEdge(Base):
    """구조 A — 정적 BOM 트리(DAG) 엣지. 한 (file_id, parent, child) = 한 엣지.

    실데이터 DAG 확정(553품번 중 81개 다중부모)이라 단일 부모 컬럼이 아닌 엣지 테이블.
    스코핑 키 = ``file_id``(한 BOM 파일 = 한 워크 범위; 실데이터 BOM이 multi-root라
    단일 루트 가정 불가). ``model``은 best-effort 라벨. ``walk_subtree`` 재귀 CTE 대상.
    """

    __tablename__ = "bom_edge"

    edge_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    file_id: Mapped[int | None] = mapped_column(
        _BIGINT_PK, ForeignKey("source_files.file_id", ondelete="CASCADE"), default=None
    )
    model: Mapped[str | None] = mapped_column(Text, default=None)
    parent_pno: Mapped[str] = mapped_column(Text, nullable=False)
    child_pno: Mapped[str] = mapped_column(Text, nullable=False)
    bom_level: Mapped[int | None] = mapped_column(Integer, default=None)
    qty: Mapped[float | None] = mapped_column(Numeric, default=None)
    source_doc_id: Mapped[int | None] = mapped_column(
        _BIGINT_PK, ForeignKey("dev_part_master.doc_id", ondelete="CASCADE"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("file_id", "parent_pno", "child_pno", name="uq_bom_edge"),
    )


class ChangeEvent(Base):
    """구조 B — 변경 이벤트 마스터. **적재 보류**(스키마만, 그룹핑 전문가 확인)."""

    __tablename__ = "change_event"

    event_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    base_model: Mapped[str | None] = mapped_column(Text, default=None)
    base_grade: Mapped[str | None] = mapped_column(Text, default=None)
    new_model: Mapped[str | None] = mapped_column(Text, default=None)
    new_grade: Mapped[str | None] = mapped_column(Text, default=None)
    event: Mapped[str | None] = mapped_column(Text, default=None)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    raw_text: Mapped[str | None] = mapped_column(Text, default=None)
    source_file: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChangeLine(Base):
    """구조 B — 이벤트별 영향 부품 라인. **적재 보류**(스키마만)."""

    __tablename__ = "change_line"

    line_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    event_id: Mapped[int | None] = mapped_column(
        _BIGINT_PK, ForeignKey("change_event.event_id", ondelete="CASCADE"), default=None
    )
    seq: Mapped[int | None] = mapped_column(Integer, default=None)
    bom_level: Mapped[int | None] = mapped_column(Integer, default=None)
    part_type: Mapped[str | None] = mapped_column(Text, default=None)
    base_pno: Mapped[str | None] = mapped_column(Text, default=None)
    new_pno: Mapped[str | None] = mapped_column(Text, default=None)
    changepoint: Mapped[str | None] = mapped_column(Text, default=None)
    embedding_dense = mapped_column(
        Vector(1024).with_variant(JSON, "sqlite"), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ToolCallLog(Base):
    """L2 도구 트레이스. 한 에이전트 실행 = 한 session_id."""

    __tablename__ = "tool_call_log"

    call_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(Text, default=None)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    arguments: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, default=None)
    result_count: Mapped[int | None] = mapped_column(Integer, default=None)
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    status: Mapped[str | None] = mapped_column(Text, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AgentFeedback(Base):
    """에이전트 추천에 대한 채택/거절 피드백."""

    __tablename__ = "agent_feedback"

    feedback_id: Mapped[int] = mapped_column(_BIGINT_PK, primary_key=True, autoincrement=True)
    session_id: Mapped[str | None] = mapped_column(Text, default=None)
    doc_id: Mapped[int | None] = mapped_column(
        _BIGINT_PK, ForeignKey("dev_part_master.doc_id", ondelete="SET NULL"), default=None
    )
    part_no: Mapped[str | None] = mapped_column(Text, default=None)
    decision: Mapped[str | None] = mapped_column(Text, default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


__all__ = [
    "AgentFeedback",
    "Base",
    "BomEdge",
    "ChangeEvent",
    "ChangeLine",
    "DevPartMaster",
    "FormRegistry",
    "IngestionLog",
    "SourceFile",
    "ToolCallLog",
]
