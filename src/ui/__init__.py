"""D-012 BOM Agent UI — 260508 Streamlit 워크플로우를 PostgreSQL backend로.

원본은 Azure OpenAI + Chroma 기반. 본 패키지는 ``rag_client``를 어댑터로
교체해 UI 코드(app.py / chatbot_flow.py / feedback_chat.py / enrich.py /
doc_packaging.py)는 무수정으로 우리 dev_part_master + Ollama bge-m3 +
``src.db.retrieve.hybrid_search``를 그대로 사용.

실행:
    .venv/Scripts/python.exe -m src.cli app run

또는 직접:
    streamlit run src/ui/app.py
"""
