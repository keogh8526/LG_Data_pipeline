"""Streamlit 페이지 — BOM 변경 영향 분석 에이전트 (L1~L4).

기존 app.py(단발 검색 + proposals)와 별개의 가산 페이지. 데이터 계층은 공유.
실행: ``streamlit run src/ui/agent_app.py`` 또는 ``python -m src.cli app agent``.

intra-ui 임포트는 app.py와 동일하게 bare(``from agent_client import ...``).
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from agent_client import run_change_analysis  # type: ignore[import-not-found]


def _doc_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        st.write("(없음)")
        return
    st.dataframe(
        [
            {
                "품번": r["pno"],
                "action": r["action"],
                "tier": r["tier"],
                "출처": r["src"],
                "설명": r["detail"],
            }
            for r in rows
        ],
        use_container_width=True,
    )


def render() -> None:
    st.set_page_config(page_title="BOM 변경 영향 분석 에이전트", layout="wide")
    st.title("BOM 변경 영향 분석 에이전트 (L1~L4)")
    st.caption(
        "변경점/사유 자유텍스트 → 영향 부품 회수 + BOM 트리 + 결정론 영향판정 + 문서 초안. "
        "신규 품번은 항상 <발번대기>, 모든 행에 [SRC]."
    )

    text = st.text_area(
        "변경점 / 변경사유", height=120, placeholder="예: 내열 강화 패킹으로 재질 변경"
    )
    if not st.button("분석 실행", type="primary") or not text.strip():
        st.info("변경 내용을 입력하고 [분석 실행]을 누르세요.")
        return

    with st.spinner("L1~L4 분석 중..."):
        try:
            res = run_change_analysis(text.strip())
        except Exception as exc:  # noqa: BLE001 — UI에 오류 표시
            st.error(f"분석 실패: {exc}")
            return

    ci = res["intent"]
    st.subheader("L1 — 구조화")
    st.write(
        f"source=`{ci['source']}` · confidence=`{ci['confidence']}` · "
        f"region=`{ci['region']}` · attribute=`{ci['change_attribute']}`"
    )
    st.write("품번:", ci["part_nos"], "  모델:", ci["models"])
    st.write("재작성 쿼리:", ci["rewritten_queries"])

    st.subheader(
        f"L2 — 회수 + 트리 (seeds={len(res['seeds'])}, tree={len(res['tree'])}, "
        f"reflections={res['reflections']}, tool_calls={res['tool_calls']})"
    )
    if res["seeds"]:
        st.dataframe(res["seeds"], use_container_width=True)
    if res["tree"]:
        with st.expander(f"BOM 트리 ({len(res['tree'])} 노드)"):
            st.dataframe(res["tree"], use_container_width=True)

    st.subheader("L3 — 영향 판정 (결정론, LLM 0회)")
    if res["verdicts"]:
        st.dataframe(
            [{**v, "rules": ", ".join(v["rules"])} for v in res["verdicts"]],
            use_container_width=True,
        )

    st.subheader("L4 — 문서 초안")
    doc = res["doc"]
    tab1, tab2, tab3, tab4 = st.tabs(
        ["변경 부품 리스트", "개발마스터 행", "BOM diff", "검토 체크리스트"]
    )
    with tab1:
        _doc_table(doc["changed_parts"])
    with tab2:
        _doc_table(doc["dev_master_rows"])
    with tab3:
        _doc_table(doc["bom_diff"])
    with tab4:
        if doc["checklist"]:
            for line in doc["checklist"]:
                st.write(line)
        else:
            st.write("(CHECK 항목 없음)")

    if res["violations"]:
        st.error("출력 검증 위반:\n" + "\n".join(f"- {v}" for v in res["violations"]))
    else:
        st.success("출력 검증 OK — 모든 행 출처 보유, NEW 품번=<발번대기>")


if __name__ == "__main__":
    render()
