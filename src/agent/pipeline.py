"""E2E 한 경로 — L1 → L2 → L3 → L4.

자유텍스트 → ChangeIntent(L1) → seeds + 트리(L2) → 영향 판정(L3) → 문서 3종(L4).
retrieval 백엔드/ LLM은 주입 가능(테스트는 fake, 운영은 DbRetrievalBackend + Ollama).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agent.docgen.generator import (
    DocItem,
    GeneratedDoc,
    generate,
    source_ref_for,
    source_ref_for_pno,
)
from src.agent.impact.models import ImpactInput, ImpactVerdict
from src.agent.impact.rules import evaluate_many
from src.agent.intent.models import ChangeIntent
from src.agent.intent.structurizer import structurize
from src.agent.llm.client import LlmClient
from src.agent.orchestrator.backend import RetrievalBackend
from src.agent.orchestrator.orchestrate import OrchestratorResult, orchestrate
from src.agent.repository.bom import BomRepository
from src.db.models import DevPartMaster


@dataclass
class AgentOutput:
    intent: ChangeIntent
    orchestration: OrchestratorResult
    verdicts: list[ImpactVerdict]
    doc: GeneratedDoc


def _build_impact_inputs(
    intent: ChangeIntent, orch: OrchestratorResult, session: Session
) -> list[ImpactInput]:
    inputs: list[ImpactInput] = []
    seen: set[str] = set()
    for s in orch.seeds:
        if not s.part_no_new:
            continue
        seen.add(s.part_no_new)
        inputs.append(
            ImpactInput(
                part_no=s.part_no_new,
                relation="seed",
                event=s.event,
                change_attribute=intent.change_attribute,
            )
        )
    # 트리 child의 part_type/classification batch 조회 → 하향 attribute 게이트 활성화
    # (없으면 게이트가 무관 자식을 구분 못해 일괄 처리됨).
    child_pnos = {t.node.pno for t in orch.tree if t.node.pno not in seen}
    attr: dict[str, tuple[str | None, str | None]] = {}
    if child_pnos:
        for pno, ptype, classif in session.execute(
            select(
                DevPartMaster.part_no_new,
                DevPartMaster.part_type,
                DevPartMaster.classification,
            ).where(DevPartMaster.part_no_new.in_(child_pnos))
        ).all():
            attr.setdefault(pno, (ptype, classif))
    for t in orch.tree:
        if t.node.pno in seen:
            continue
        seen.add(t.node.pno)  # 중복 child도 1회만
        ptype, classif = attr.get(t.node.pno, (None, None))
        inputs.append(
            ImpactInput(
                part_no=t.node.pno,
                relation="child",
                depth=t.node.depth,
                change_attribute=intent.change_attribute,
                part_type=ptype,
                classification=classif,
            )
        )
    return inputs


def _build_doc_items(
    session: Session,
    orch: OrchestratorResult,
    verdicts: list[ImpactVerdict],
) -> list[DocItem]:
    by_pno: dict[str, ImpactVerdict] = {v.part_no: v for v in verdicts}
    items: list[DocItem] = []
    seen: set[str] = set()
    for s in orch.seeds:
        pno = s.part_no_new
        if not pno or pno not in by_pno:
            continue
        seen.add(pno)
        v = by_pno[pno]
        items.append(
            DocItem(
                part_no=pno,
                is_new=(s.event == "New"),
                action=v.action,
                tier=v.tier,
                source=source_ref_for(session, s.doc_id),
                relation="seed",
                part_name=s.part_name,
                model=s.new_model,
                reason="; ".join(f.reason for f in v.findings[:2]),
            )
        )
    for t in orch.tree:
        pno = t.node.pno
        if pno in seen or pno not in by_pno:
            continue
        seen.add(pno)
        v = by_pno[pno]
        items.append(
            DocItem(
                part_no=pno,
                is_new=False,
                action=v.action,
                tier=v.tier,
                source=source_ref_for_pno(session, pno),
                relation="child",
                reason="; ".join(f.reason for f in v.findings[:2]),
            )
        )
    return items


def run_agent(
    text: str,
    *,
    session: Session,
    backend: RetrievalBackend,
    bom_repo: BomRepository | None = None,
    llm: LlmClient | None = None,
    session_id: str | None = None,
) -> AgentOutput:
    """L1~L4 한 경로 실행."""
    intent = structurize(text, llm=llm)
    orch = orchestrate(
        intent, session=session, backend=backend, bom_repo=bom_repo, session_id=session_id
    )
    verdicts = evaluate_many(_build_impact_inputs(intent, orch, session))
    doc = generate(_build_doc_items(session, orch, verdicts), llm=llm)
    return AgentOutput(intent=intent, orchestration=orch, verdicts=verdicts, doc=doc)
