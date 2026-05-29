"""L3 경계 타입 (순수 dataclass — LLM/Pydantic 불필요, 결정론)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Action = Literal["MODIFY", "ADD", "DELETE", "KEEP", "CHECK"]
Tier = Literal["CORE", "CASCADE"]
Relation = Literal["seed", "child", "parent"]


@dataclass
class ImpactInput:
    """영향도 판정 입력 1건 (한 부품 + 변경 맥락).

    relation: seed=직접 변경 부품, child=하향(자식), parent=상향(부모).
    *_changed / c_osp / drbfm / hsms / r_up_break: 룰 트리거 플래그.
    attribute_related: None이면 attribute_part_groups 통제 어휘로 자동 판정.
    """

    part_no: str
    relation: Relation = "seed"
    depth: int = 0
    event: str | None = None
    change_attribute: str | None = None
    part_type: str | None = None
    classification: str | None = None
    uit_changed: bool = False
    supply_type_changed: bool = False
    c_osp: bool = False
    drbfm: bool = False
    hsms: bool = False
    r_up_break: bool = False
    attribute_related: bool | None = None


@dataclass
class Finding:
    """발화한 판단 1건 (룰 또는 구조 로직). trace에 전부 표시."""

    rule_id: str
    action: Action
    tier: Tier
    priority: int
    reason: str


@dataclass
class ImpactVerdict:
    """최종 판정. action/tier는 최우선 finding, findings는 priority desc 전체."""

    part_no: str
    action: Action
    tier: Tier
    findings: list[Finding] = field(default_factory=list)
