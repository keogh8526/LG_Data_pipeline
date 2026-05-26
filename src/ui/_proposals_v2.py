"""D-012 simplified proposal generator (옵션 A).

원본 generate_proposals_from_docs (app.py ~700 lines)는:
  - 모든 doc text에 [L1] 마커와 - 부품 라인이 있다는 가정
  - 모든 doc이 RSN/CHG 100% 채워져 있다는 가정 (변경부품 히스토리만)
  - MAIN_PART_ALIASES / OBJECT_ALIASES 정교한 키워드 사전
  - CORE/CORE_GROUP/CASCADE/EXCLUDE 4단계 점진적 복구

D-012 backend는 BOM + base_master + 변경부품 혼합 인덱싱 → 위 가정이 모두
깨짐. 한 줄씩 fix해도 다음 strict 필터가 막는 mole-whack 상태.

이 모듈은 rag_client.retrieve_docs 결과(hit dict list)를 직접 받아
**텍스트 파싱 없이 meta dict만 사용**해서 proposal을 생성. 단순/명시적
파이프라인:

  1. dedup by part_no_new
  2. base_snapshot 대조 → in_base / action 결정
  3. 사용자 변경점 토큰과 desc/rsn/chg 매치 강도 계산
  4. CORE (매치 1+) / CASCADE 분리
  5. 1 proposal 반환 (UI 호환 형식)

원본과 호환되는 dict 키 구조를 유지해서 merge_proposals_order_independent /
proposal 표시 코드는 그대로 동작.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[._\-][A-Za-z0-9]+)?|[가-힣]{2,}")


def _tokens(text: str) -> set[str]:
    """소문자 + 한글 2글자 이상 / 영문/숫자 단어 추출."""
    return {t.lower() for t in _TOKEN_RE.findall(str(text or "").lower())}


def _norm_pno(s: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper())


def _dedup_hits_by_part(hits: list[dict]) -> list[dict]:
    """같은 part_no_new는 첫 (가장 좋은 score) hit만 남김."""
    seen = set()
    out = []
    for h in hits:
        meta = h.get("meta") or {}
        pno_key = _norm_pno(meta.get("part_no_new") or meta.get("part_no") or "")
        if not pno_key:
            pno_key = "ID::" + str(h.get("id") or "")
        if pno_key in seen:
            continue
        seen.add(pno_key)
        out.append(h)
    return out


def _snapshot_field(snap: Any, key: str, default: Any = None) -> Any:
    """BaseMasterSnapshot은 dataclass, 직접 만든 dict도 호환되게 양쪽 access."""
    if snap is None:
        return default
    if isinstance(snap, dict):
        return snap.get(key, default)
    # dataclass / attribute access
    return getattr(snap, key, default)


def _index_base_snapshot(base_snapshot: Any) -> tuple[dict, dict]:
    """base BOM rows → (pno_norm → row, desc_upper → row 리스트) 인덱스."""
    rows = _snapshot_field(base_snapshot, "rows") or []
    by_pno: dict[str, dict] = {}
    by_desc: dict[str, list[dict]] = {}
    for r in rows:
        pn = str(r.get("part_no") or "").strip()
        d = str(r.get("part_name") or "").strip()
        if pn:
            by_pno[_norm_pno(pn)] = r
        if d:
            by_desc.setdefault(d.upper(), []).append(r)
    return by_pno, by_desc


def _hit_to_part(
    hit: dict,
    base_pno_idx: dict,
    base_desc_idx: dict,
    change_tokens: set[str],
) -> dict:
    """hit dict → proposal changed_parts entry."""
    meta = hit.get("meta") or {}
    pno_new = meta.get("part_no_new") or meta.get("part_no") or ""
    pno_base = meta.get("part_no_base") or ""
    desc = meta.get("part_name") or meta.get("desc") or ""
    new_model = meta.get("new_model") or ""
    form_id = meta.get("form_id") or ""
    event = meta.get("event") or ""
    chg = meta.get("change_point_raw") or ""
    rsn = meta.get("change_reason_raw") or ""

    pno_key = _norm_pno(pno_new) or _norm_pno(pno_base)
    base_row = base_pno_idx.get(pno_key) if pno_key else None
    in_base = base_row is not None
    if not in_base and desc:
        cand = base_desc_idx.get(desc.upper())
        if cand:
            base_row = cand[0]
            in_base = True

    if pno_base and pno_new and pno_base != pno_new:
        action = "MODIFY"
    elif event == "New" or (pno_new and not pno_base and event != "Carry-over"):
        action = "ADD"
    elif event == "Carry-over":
        action = "KEEP"
    else:
        action = "CHECK"
    if action == "ADD" and in_base:
        action = "CHECK"

    if in_base:
        sourcing = ""
        display_pno = pno_new or pno_base
    elif pno_new:
        sourcing = "참고품번"
        display_pno = pno_new
    else:
        sourcing = "신규"
        display_pno = "(채번 필요)"

    blob = " ".join([desc, rsn, chg, new_model]).lower()
    match_score = sum(
        1 for tok in change_tokens if tok and len(tok) >= 2 and tok in blob
    )

    return {
        "part_name": desc,
        "part_no": pno_new or pno_base,
        "display_pno": display_pno,
        "action": action,
        "in_base": in_base,
        "rsn": rsn,
        "chg": chg,
        "base_type": str((base_row or {}).get("part_type", "") or "") if base_row else "",
        "lvl": str((base_row or {}).get("bom_depth", "") or "") if base_row else "",
        "qty": str((base_row or {}).get("qty_new", "") or "1") if base_row else "1",
        "tier": "CORE" if match_score >= 1 else "CASCADE",
        "source_doc": form_id,
        "sourcing": sourcing,
        "sourcing_reason": rsn or chg,
        "l1_desc": (base_row or {}).get("part_name", "") if base_row else "",
        "skeleton_order": 999999,
        "_match_score": match_score,
        "_score_rrf": float(meta.get("score_rrf") or 0.0),
        "_score_semantic": float(meta.get("score_semantic") or 0.0),
    }


_STOP_WORDS = {
    "에", "를", "을", "의", "에서", "로", "으로", "및", "또는", "그리고",
    "추가", "변경", "교체", "적용", "개선", "기능", "신규", "디자인",
    "add", "change", "modify", "for", "and", "the", "with", "new",
}


def generate_proposals_from_hits(
    hits: list[dict],
    base_snapshot: dict | None,
    change_items: list[str],
    intent: dict | None = None,
) -> list[dict]:
    """D-012 simplified — retrieve hits → proposals.

    Args:
        hits: rag_client.retrieve_docs 결과 list (id/dist/meta/text).
        base_snapshot: app.py의 make_base_snapshot 결과 dict.
        change_items: 사용자 변경점 자유 텍스트 list.
        intent: parse_change_intent 결과 (None OK).

    Returns:
        proposal dict list. 원본 함수와 동일 키 구조 — UI 그대로 동작.
    """
    if not hits or not change_items:
        return []

    # base_snapshot은 dict이거나 BaseMasterSnapshot dataclass 둘 다 가능.
    # None일 땐 빈 dict로 normalize (이후 _snapshot_field가 dict.get으로 처리).
    if base_snapshot is None:
        base_snapshot = {}
    intent = intent or {
        "raw_text": "\n".join(change_items),
        "target_object": "",
        "action": "ADD",
    }

    # 사용자 변경점 토큰
    change_tokens: set[str] = set()
    for item in change_items:
        change_tokens |= _tokens(item)
    change_tokens = {t for t in change_tokens if len(t) >= 2 and t not in _STOP_WORDS}

    base_pno_idx, base_desc_idx = _index_base_snapshot(base_snapshot)

    deduped = _dedup_hits_by_part(hits)
    parts_all = [
        _hit_to_part(h, base_pno_idx, base_desc_idx, change_tokens)
        for h in deduped
    ]

    # 매치 강도 + RRF 점수로 ranking
    parts_all.sort(
        key=lambda p: (-p.get("_match_score", 0), -p.get("_score_rrf", 0)),
    )

    changed = [p for p in parts_all if p.get("tier") == "CORE"][:25]
    indirect = [p for p in parts_all if p.get("tier") == "CASCADE"][:25]

    # CORE 부족 시 top score CASCADE를 changed로 promote
    if len(changed) < 5 and indirect:
        promoted = indirect[: 5 - len(changed)]
        changed.extend(promoted)
        indirect = indirect[len(promoted):]

    if not changed and not indirect:
        return []

    source_docs = sorted(
        {p.get("source_doc", "") for p in changed + indirect if p.get("source_doc")}
    )
    ref_models: list[str] = []
    for h in deduped[:30]:
        m = (h.get("meta") or {}).get("new_model")
        if m and m not in ref_models:
            ref_models.append(m)
    ref_models = ref_models[:5]

    # base L1 추정 — base_snapshot의 첫 lvl=.1 row
    base_l1_desc = ""
    base_l1_pno = ""
    for r in _snapshot_field(base_snapshot, "rows") or []:
        if str(r.get("lvl", "")).strip() in (".1", "1"):
            base_l1_desc = r.get("part_name") or ""
            base_l1_pno = r.get("part_no") or ""
            break

    # 내부 score 필드 제거
    def _clean(p: dict) -> dict:
        return {k: v for k, v in p.items() if not k.startswith("_")}

    proposal = {
        "proposal_id": "P-001",
        "status": "PENDING",
        "change_summary": intent.get("raw_text") or "\n".join(change_items),
        "target": {
            "main_object": intent.get("target_object", ""),
            "action": intent.get("action", "ADD"),
        },
        "lvl1": {
            "desc": base_l1_desc,
            "part_no": base_l1_pno,
            "in_base": bool(base_l1_pno),
            "user_action": "",
            "skip": False,
        },
        "changed_parts": [_clean(p) for p in changed],
        "indirect_parts": [_clean(p) for p in indirect],
        "existing_parts": [],
        "confidence": round(len(changed) / max(len(parts_all), 1), 2),
        "source_docs": source_docs,
        "ref_models": ref_models,
    }
    return [proposal]


__all__ = ["generate_proposals_from_hits"]
