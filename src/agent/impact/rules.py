"""L3 결정론 룰 엔진 — config/impact_rules.yaml 기반.

LLM import 절대 금지(설계 §5-1). 입력 ImpactInput → 구조적 cascade finding +
YAML 룰 발화 finding → priority 정렬 → 최우선 action/tier. trace엔 발화 전부 표시.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any, cast

import yaml

from src.agent.impact.models import Action, Finding, ImpactInput, ImpactVerdict, Tier
from src.utils.paths import CONFIG_DIR

IMPACT_RULES_PATH = CONFIG_DIR / "impact_rules.yaml"

# 충돌 tie-break (priority 같을 때 더 영향 큰 action 우선).
_ACT_RANK: dict[str, int] = {"DELETE": 5, "ADD": 4, "MODIFY": 3, "CHECK": 2, "KEEP": 1}
_VALID_TIERS = {"CORE", "CASCADE"}


@lru_cache(maxsize=1)
def _config() -> dict[str, Any]:
    cfg: dict[str, Any] = yaml.safe_load(IMPACT_RULES_PATH.read_text(encoding="utf-8"))
    # config 무결성 검증 (프로그래머/설정 오류 → raise).
    for rule in cfg.get("rules", []):
        then = rule.get("then", {})
        if then.get("action") not in _ACT_RANK or then.get("tier") not in _VALID_TIERS:
            raise ValueError(f"impact_rules.yaml: invalid then in {rule.get('id')!r}")
    return cfg


def _attribute_related(inp: ImpactInput) -> bool:
    """하향 통제 어휘로 자식의 변경 관련성 판정. attribute 모르면 보수적(True)."""
    if inp.attribute_related is not None:
        return inp.attribute_related
    if not inp.change_attribute:
        return True
    groups: list[str] = _config().get("attribute_part_groups", {}).get(
        inp.change_attribute, []
    )
    return inp.part_type in groups or inp.classification in groups


def _structural(inp: ImpactInput) -> Finding:
    """relation 기반 구조적 기본 판정 (룰과 별개, 항상 1건)."""
    if inp.relation == "seed":
        mapping: dict[str | None, tuple[Action, str]] = {
            "New": ("ADD", "신규 부품"),
            "Change": ("MODIFY", "변경 부품"),
            "Carry-over": ("KEEP", "유지 부품"),
        }
        action, why = mapping.get(inp.event, ("CHECK", "event 미상 — 검토"))
        return Finding("STRUCT_seed", action, "CORE", 50, f"seed:{why}")
    if inp.relation == "child":
        if _attribute_related(inp):
            return Finding("STRUCT_child_related", "CHECK", "CASCADE", 40, "변경 속성 관련 자식 — 검토")
        return Finding("STRUCT_child_unrelated", "KEEP", "CASCADE", 40, "무관 자식 — KEEP(cascade 차단)")
    # parent
    if inp.r_up_break:
        return Finding("STRUCT_parent_chaeban", "ADD", "CASCADE", 40, "상향 호환 깨짐 → 부모 채반(NEW)")
    return Finding("STRUCT_parent_keep", "KEEP", "CASCADE", 40, "상향 호환 유지 → KEEP")


def _matches(when: dict[str, Any], inp: ImpactInput) -> bool:
    val = getattr(inp, when["field"], None)
    if "equals" in when:
        return bool(val == when["equals"])
    if "in" in when:
        return val in when["in"]
    if "truthy" in when:
        return bool(val) == bool(when["truthy"])
    return False


def evaluate(inp: ImpactInput) -> ImpactVerdict:
    """단일 부품 영향도 판정. findings는 priority desc 정렬, 전부 노출."""
    findings: list[Finding] = [_structural(inp)]
    for rule in _config().get("rules", []):
        if _matches(rule["when"], inp):
            findings.append(
                Finding(
                    rule_id=rule["id"],
                    action=cast(Action, rule["then"]["action"]),
                    tier=cast(Tier, rule["then"]["tier"]),
                    priority=int(rule.get("priority", 0)),
                    reason=rule["description"],
                )
            )
    findings.sort(key=lambda f: (f.priority, _ACT_RANK[f.action]), reverse=True)
    top = findings[0]
    return ImpactVerdict(part_no=inp.part_no, action=top.action, tier=top.tier, findings=findings)


def evaluate_many(inputs: list[ImpactInput]) -> list[ImpactVerdict]:
    return [evaluate(i) for i in inputs]
