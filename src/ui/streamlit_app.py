"""Step 10 — Streamlit MVP UI.

A thin front end over the FastAPI backend: enter a change point, view the
candidate models / BOM draft / master draft, inspect the audit log, and export.
Run with: ``streamlit run src/ui/streamlit_app.py``.
"""

from __future__ import annotations

import os

import requests
import streamlit as st

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")


def main() -> None:
    """Render the Streamlit MVP page."""
    st.set_page_config(page_title="LG BOM Agent", layout="wide")
    st.title("LG 개발부품 BOM Agent — MVP")

    change_point = st.text_area("변경점 입력", height=120)

    if st.button("초안 생성", type="primary"):
        if not change_point.strip():
            st.warning("변경점을 입력하세요.")
            return
        try:
            response = requests.post(
                f"{API_BASE}/api/draft",
                json={"change_point": change_point},
                timeout=120,
            )
        except requests.RequestException as exc:
            st.error(f"백엔드 연결 실패: {exc}")
            return

        if response.status_code == 501:
            st.info(
                "초안 생성은 로컬 LLM 연동 후 활성화됩니다 (DECISIONS D-003). "
                "현재는 인터페이스 골격입니다."
            )
            return
        if not response.ok:
            st.error(f"오류 {response.status_code}: {response.text}")
            return

        data = response.json()
        st.subheader("후보 모델")
        st.json(data.get("candidate_models", []))
        st.subheader("BOM 초안")
        st.json(data.get("bom_draft", []))
        st.subheader("Master 초안")
        st.json(data.get("master_draft", {}))
        with st.expander("Audit log"):
            st.write(data.get("audit_log", []))


if __name__ == "__main__":
    main()
