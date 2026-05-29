"""L1 경계 스키마 (Pydantic v2).

``ChangeIntent`` = structurizer 최종 출력. ``LlmSlots`` = LLM이 채우는 의미 슬롯의
스키마 경계 — 잘못된 모양(타입/구조)이면 ValidationError로 거부되어 structurizer가
결정론 fallback으로 떨어진다(설계 §3-2, "잘못된 JSON 거부").
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

IntentSource = Literal["regex+llm", "regex", "raw_fallback"]

_MAX_QUERIES = 4


class LlmSlots(BaseModel):
    """LLM JSON 모드가 반환해야 하는 의미 슬롯. extra 키는 무시, 타입 위반은 거부."""

    model_config = {"extra": "ignore"}

    change_attribute: str | None = None
    change_direction: str | None = None
    intent_summary: str = ""
    rewritten_queries: list[str] = Field(default_factory=list)
    confidence: float = 0.5

    @field_validator("confidence")
    @classmethod
    def _clamp(cls, v: float) -> float:
        return max(0.0, min(1.0, v))

    @field_validator("rewritten_queries")
    @classmethod
    def _trim_queries(cls, v: list[str]) -> list[str]:
        seen: list[str] = []
        for q in v:
            q = q.strip()
            if q and q not in seen:
                seen.append(q)
        return seen[:_MAX_QUERIES]


class ChangeIntent(BaseModel):
    """L1 최종 산출물. 정규식 선추출(결정론) + LLM 의미 슬롯(게이트) 병합."""

    model_config = {"extra": "ignore"}

    raw_text: str
    part_nos: list[str] = Field(default_factory=list)
    models: list[str] = Field(default_factory=list)
    region: str | None = None
    change_attribute: str | None = None
    change_direction: str | None = None
    intent_summary: str = ""
    rewritten_queries: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    source: IntentSource = "regex"

    @field_validator("confidence")
    @classmethod
    def _clamp_conf(cls, v: float) -> float:
        return max(0.0, min(1.0, v))
