"""v2.0 §7-1 — Multi-Vector 임베딩 (5개 벡터).

preprocessing_v2.md §7. 한 ChangeEvent당 최대 5개 벡터:
  narrative_emb     항상 (narrative_text 전체)
  change_point_emb  core.change_point 있을 때
  change_reason_emb core.change_reason 있을 때
  drbfm_emb         payload에 DRBFM 코멘트 있을 때
  test_plan_emb     payload 시험 관련 키들 결합 텍스트 있을 때

ENABLE_EMBEDDING=1 게이트.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

import yaml

from src.embed.embedder import embed_texts
from src.utils.logging import get_logger
from src.utils.paths import COLUMN_DICTIONARY_PATH

log = get_logger(__name__)


@dataclass
class EmbeddingPlan:
    """한 ChangeEvent의 multi-vector 임베딩 결과."""

    narrative_emb: list[float] | None = None
    change_point_emb: list[float] | None = None
    change_reason_emb: list[float] | None = None
    drbfm_emb: list[float] | None = None
    test_plan_emb: list[float] | None = None


# ── payload에서 DRBFM / 시험 텍스트 모으기 ─────────────────────────

_DRBFM_KEYS = ("DRBFM > DRBFM 코멘트", "DRBFM > 코멘트", "DRBFM")


def _load_test_plan_keys() -> list[str]:
    try:
        data = yaml.safe_load(COLUMN_DICTIONARY_PATH.read_text(encoding="utf-8"))
        return list(data.get("test_plan_keys", {}).get("source_keys", []))
    except Exception:  # noqa: BLE001
        return [
            "시험 > 부품인정시험 항목",
            "시험 > 시험 항목",
            "시험 > 시험 책임자",
        ]


def _get_first(payload: dict[str, Any] | None, keys: Iterable[str]) -> str | None:
    if not payload:
        return None
    for k in keys:
        # exact 매치 + path 끝 매치
        for actual, value in payload.items():
            if actual == k or actual.endswith(f" > {k.split(' > ')[-1]}"):
                if value is not None and str(value).strip():
                    return str(value).strip()
    return None


def collect_drbfm_text(payload: dict[str, Any] | None) -> str | None:
    return _get_first(payload, _DRBFM_KEYS)


def collect_test_plan_text(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    keys = _load_test_plan_keys()
    parts: list[str] = []
    for k in keys:
        for actual, value in payload.items():
            if actual == k or actual.endswith(f" > {k.split(' > ')[-1]}"):
                if value is not None and str(value).strip():
                    parts.append(str(value).strip())
    return " | ".join(parts) if parts else None


# ── Batch embedding ───────────────────────────────────────────────


def _build_texts_for_event(
    narrative: str | None,
    change_point: str | None,
    change_reason: str | None,
    drbfm: str | None,
    test_plan: str | None,
) -> dict[str, str | None]:
    return {
        "narrative_emb": narrative or "",
        "change_point_emb": change_point,
        "change_reason_emb": change_reason,
        "drbfm_emb": drbfm,
        "test_plan_emb": test_plan,
    }


def embed_change_event_rows(events: list[Any]) -> list[EmbeddingPlan]:
    """``ChangeEvent`` (ORM 또는 dict) 리스트 → ``EmbeddingPlan`` 리스트.

    빈/None 텍스트는 임베딩 안 함. 같은 위치 인덱스 유지.
    """
    # 각 벡터별로 (event_idx, text) 모음 → 한 번에 batch embed
    per_field: dict[str, list[tuple[int, str]]] = {
        "narrative_emb": [],
        "change_point_emb": [],
        "change_reason_emb": [],
        "drbfm_emb": [],
        "test_plan_emb": [],
    }

    plans: list[EmbeddingPlan] = [EmbeddingPlan() for _ in events]

    for idx, e in enumerate(events):
        narrative = getattr(e, "narrative_text", None) or (e.get("narrative_text") if isinstance(e, dict) else None)
        change_point = getattr(e, "change_point", None) or (e.get("change_point") if isinstance(e, dict) else None)
        change_reason = getattr(e, "change_reason", None) or (e.get("change_reason") if isinstance(e, dict) else None)
        payload = getattr(e, "payload", None) or (e.get("payload") if isinstance(e, dict) else None)
        drbfm = collect_drbfm_text(payload)
        test_plan = collect_test_plan_text(payload)

        texts = _build_texts_for_event(narrative, change_point, change_reason, drbfm, test_plan)
        for field_name, txt in texts.items():
            if txt:
                per_field[field_name].append((idx, txt))

    for field_name, items in per_field.items():
        if not items:
            continue
        indices = [i for i, _ in items]
        texts = [t for _, t in items]
        try:
            vectors = embed_texts(texts)
        except RuntimeError as exc:
            log.warning("multi_vector.embed_disabled", field=field_name, error=str(exc))
            return plans
        for idx, vec in zip(indices, vectors, strict=True):
            setattr(plans[idx], field_name, vec)

    return plans
