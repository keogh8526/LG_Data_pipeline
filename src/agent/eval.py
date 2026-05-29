"""Phase 6 — 평가 골격.

retrieval Top-k recall/precision/MRR + L3 룰 발화 FP(오발화) 측정. 목표치는 placeholder
— **측정 인프라 자체가 산출물**. 골든 라벨은 change_event(구조 B) 의존이라 적재 전까지는
수기 미니 골든셋(GOLDEN_MINI, 전문가 검증 필요)으로 대체.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.agent.impact.models import ImpactVerdict


def recall_at_k(retrieved: list[str], golden: set[str], k: int) -> float:
    if not golden:
        return 0.0
    return len(set(retrieved[:k]) & golden) / len(golden)


def precision_at_k(retrieved: list[str], golden: set[str], k: int) -> float:
    if k <= 0:
        return 0.0
    return len(set(retrieved[:k]) & golden) / k


def mrr(retrieved: list[str], golden: set[str]) -> float:
    for i, pno in enumerate(retrieved):
        if pno in golden:
            return 1.0 / (i + 1)
    return 0.0


def rule_fp_rate(verdicts: list[ImpactVerdict], should_keep: set[str]) -> float:
    """should_keep(유지여야 할) 부품 중 action != KEEP 비율 = 룰 오발화율."""
    relevant = [v for v in verdicts if v.part_no in should_keep]
    if not relevant:
        return 0.0
    fp = sum(1 for v in relevant if v.action != "KEEP")
    return fp / len(relevant)


@dataclass
class RetrievalMetrics:
    recall_at_k: float
    precision_at_k: float
    mrr: float
    k: int


def evaluate_retrieval(retrieved: list[str], golden: set[str], *, k: int = 5) -> RetrievalMetrics:
    return RetrievalMetrics(
        recall_at_k=recall_at_k(retrieved, golden, k),
        precision_at_k=precision_at_k(retrieved, golden, k),
        mrr=mrr(retrieved, golden),
        k=k,
    )


@dataclass
class GoldenCase:
    text: str
    golden_pnos: set[str]
    should_keep: set[str] = field(default_factory=set)


# 골든 미니셋 placeholder — TODO(expert): change_event 적재/전문가 확인 후 실제 라벨로 채움.
GOLDEN_MINI: list[GoldenCase] = []
