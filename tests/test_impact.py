"""Phase 4 — L3 Impact Analyzer 테스트 (결정론, 표 기반).

검증: 룰 발화 + priority 충돌 해소, 하향 attribute 게이트(무관 자식 KEEP),
상향 form-fit-function 채반, 발화 trace 전부 표시, **LLM import 0 (코드로 보장)**.
"""

from __future__ import annotations

import inspect

import pytest

import src.agent.impact.models as impact_models
import src.agent.impact.rules as impact_rules
from src.agent.impact import ImpactInput, evaluate


@pytest.mark.parametrize(
    ("inp", "action", "tier", "expect_rule"),
    [
        (ImpactInput("P", relation="seed", event="Change"), "MODIFY", "CORE", "R08_change_part"),
        (ImpactInput("P", relation="seed", event="New"), "ADD", "CORE", "R07_new_part"),
        (ImpactInput("P", relation="seed", event="Carry-over"), "KEEP", "CORE", "R09_carryover"),
        # drbfm(prio85) overrides Change MODIFY(55) → CHECK
        (ImpactInput("P", relation="seed", event="Change", drbfm=True), "CHECK", "CORE", "R04_drbfm"),
        (ImpactInput("P", relation="seed", event="Change", uit_changed=True), "CHECK", "CORE", "R01_uit_change"),
        (ImpactInput("P", relation="seed", supply_type_changed=True), "CHECK", "CORE", "R02_supply_type_change"),
        # 상향 채반(prio90) → 부모 NEW(ADD)
        (ImpactInput("P", relation="parent", r_up_break=True), "ADD", "CASCADE", "R06_r_up_chaeban"),
    ],
)
def test_rule_firing(inp, action, tier, expect_rule):
    v = evaluate(inp)
    assert v.action == action
    assert v.tier == tier
    assert expect_rule in {f.rule_id for f in v.findings}


def test_downward_unrelated_child_keep():
    # change_attribute=재질 → 관련 군 {사출,단품}. part_type=전장 → 무관 → KEEP.
    v = evaluate(ImpactInput("C", relation="child", change_attribute="재질", part_type="전장"))
    assert v.action == "KEEP"
    assert any(f.rule_id == "STRUCT_child_unrelated" for f in v.findings)


def test_downward_related_child_check():
    v = evaluate(ImpactInput("C", relation="child", change_attribute="재질", part_type="사출"))
    assert v.action == "CHECK"


def test_upward_keep_when_compat_holds():
    v = evaluate(ImpactInput("P", relation="parent", r_up_break=False))
    assert v.action == "KEEP"


def test_conflict_shows_all_findings_and_highest_priority_wins():
    v = evaluate(
        ImpactInput("P", relation="seed", event="Change", drbfm=True, uit_changed=True)
    )
    ids = {f.rule_id for f in v.findings}
    assert {"STRUCT_seed", "R08_change_part", "R04_drbfm", "R01_uit_change"} <= ids
    assert v.action == "CHECK"  # R04 drbfm prio 85 최고
    # findings는 priority desc 정렬
    priorities = [f.priority for f in v.findings]
    assert priorities == sorted(priorities, reverse=True)


def test_impact_module_imports_no_llm():
    """L3는 LLM/네트워크를 절대 import하지 않는다 (결정론 보장)."""
    for mod in (impact_rules, impact_models):
        src = inspect.getsource(mod).lower()
        assert "agent.llm" not in src
        assert "ollama" not in src
        assert "import requests" not in src
