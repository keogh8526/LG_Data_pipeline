"""v2.0 §7-3 — Reranker (bge-reranker-v2-m3-ko).

dense+sparse 후보 30개 → 상위 5개로 재정렬. Ollama가 reranker를 띄우지 않으면
sentence-transformers의 CrossEncoder로 fallback. ENABLE_RERANKER=1 게이트.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from src.utils.logging import get_logger

log = get_logger(__name__)

DEFAULT_MODEL = "dragonkue/bge-reranker-v2-m3-ko"


def reranker_enabled() -> bool:
    return os.environ.get("ENABLE_RERANKER", "0") == "1"


@dataclass
class RerankCandidate:
    """rerank 입력."""

    id: str
    text: str
    extra: dict | None = None


@dataclass
class RerankResult:
    """rerank 출력."""

    id: str
    score: float
    text: str
    extra: dict | None = None


@lru_cache(maxsize=1)
def _load_cross_encoder(model_name: str) -> object:
    from sentence_transformers import CrossEncoder

    log.info("rerank.load", model=model_name)
    return CrossEncoder(model_name)


def rerank(query: str, candidates: list[RerankCandidate], top_k: int = 5) -> list[RerankResult]:
    """``query``에 대해 candidates 재정렬. ENABLE_RERANKER 미설정 시 점수 동일하게 유지."""
    if not candidates:
        return []
    if not reranker_enabled():
        log.info("rerank.disabled — pass-through")
        return [
            RerankResult(id=c.id, score=1.0 - i / max(1, len(candidates)), text=c.text, extra=c.extra)
            for i, c in enumerate(candidates[:top_k])
        ]

    model_name = os.environ.get("RERANKER_MODEL", DEFAULT_MODEL)
    model = _load_cross_encoder(model_name)
    pairs = [(query, c.text) for c in candidates]
    scores = model.predict(pairs)  # type: ignore[attr-defined]
    scored = sorted(
        zip(candidates, scores, strict=True),
        key=lambda x: float(x[1]),
        reverse=True,
    )
    return [
        RerankResult(id=c.id, score=float(s), text=c.text, extra=c.extra)
        for c, s in scored[:top_k]
    ]
