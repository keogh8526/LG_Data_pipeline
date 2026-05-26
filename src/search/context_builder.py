"""v2.0 §2-2 [B] — LLM Context Builder.

검색 hit들의 (narrative + core + payload 전체 + 인접 그래프)를 LLM 프롬프트
컨텍스트로 직렬화. "디테일 회수" 경로(payload의 비검색 컬럼도 답변에 사용).
"""

from __future__ import annotations

import json
from typing import Any

from src.search.pipeline import SearchHit


_HEADER = "다음은 검색된 변경 이벤트 정보입니다. 각 hit는 narrative(검색 이유) + core 컬럼 + payload 원본 전체 + 인접 그래프 노드를 포함합니다."


def build_llm_context(
    hits: list[SearchHit],
    max_payload_chars_per_hit: int = 4000,
    include_neighbors: bool = True,
) -> str:
    """검색 결과 → LLM 프롬프트용 자연어 + 구조화 텍스트."""
    if not hits:
        return _HEADER + "\n\n(검색 결과 없음)"

    parts: list[str] = [_HEADER, ""]
    for idx, hit in enumerate(hits, start=1):
        parts.append(f"## hit {idx} — event_id={hit.event_id} score={hit.score:.3f}")
        if hit.narrative_text:
            parts.append(f"**narrative**: {hit.narrative_text}")
        # core 요약
        core = {
            "part_no": hit.part_no,
            "new_model_code": hit.new_model_code,
            "form_version": hit.form_version,
            "change_point": hit.change_point,
            "change_reason": hit.change_reason,
        }
        parts.append("**core**:")
        parts.append("```json")
        parts.append(json.dumps({k: v for k, v in core.items() if v}, ensure_ascii=False, indent=2))
        parts.append("```")
        # payload 전체 (길이 제한)
        if hit.payload:
            payload_json = json.dumps(hit.payload, ensure_ascii=False, indent=2)
            if len(payload_json) > max_payload_chars_per_hit:
                payload_json = payload_json[:max_payload_chars_per_hit] + "\n... (truncated)"
            parts.append("**payload (양식 원본 전체)**:")
            parts.append("```json")
            parts.append(payload_json)
            parts.append("```")
        if include_neighbors and hit.graph_neighbors:
            parts.append("**인접 그래프 (BOM / change-chain 1-hop)**:")
            for n in hit.graph_neighbors:
                parts.append(f"- {n.get('relation')}: {n.get('event_id')}")
        parts.append("")
    return "\n".join(parts)
