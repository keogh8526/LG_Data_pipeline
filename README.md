# LG_Data_pipeline

LG 가전 신규 모델 개발 시, 기존 양산 모델(Base) 대비 부품 변경을 추적하는
개발부품 마스터/BOM 문서를 표준화·DB화·검색 가능하게 만드는 데이터 파이프라인.

## 개요

양식 4종(20col / 56col / 96col / 통합 v1.2)으로 흩어진 개발부품 마스터를 정답
ontology(통합 v1.2)로 매핑하고, **Neo4j 단독 스토어**(그래프 + 벡터 인덱스)에
적재한 뒤, **LangGraph** 에이전트로 변경점 입력 → BOM/마스터 초안을 생성한다.

MVP 아키텍처 결정 배경은 [`DECISIONS.md`](./DECISIONS.md) D-005/D-006 참조.

## MVP 기술 스택

| 계층 | 기술 |
|---|---|
| Frontend | Streamlit |
| Backend | FastAPI |
| Orchestration | LangGraph (5노드 state machine) |
| LLM | Ollama (Qwen 2.5 32B / 7B) |
| Embedding | BGE-M3 (Ollama, 1024 dim) |
| Storage | Neo4j 5.x Community (그래프 + 네이티브 vector index) |
| Ontology/Rules | JSON Schema + Pydantic + axioms (코드) |

PostgreSQL·Qdrant는 MVP 범위 밖이며, 분리 트리거 도달 시 v1.5+에서 재도입 검토
(DECISIONS D-006).

## 요구 사항

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (패키지 매니저)
- Docker / Docker Compose

## 설치

```bash
uv sync --extra dev          # 핵심 + 개발 의존성
uv sync --extra embed        # BGE-M3 sentence-transformers fallback (~2GB)
uv sync --extra structured   # outlines (schema-guided 출력 강제)
uv sync --extra llm          # openai (양식 매핑 룰 1회성 생성용)
cp .env.example .env
```

## 로컬 구동

```bash
docker compose up -d         # Neo4j + Ollama + FastAPI + Streamlit
docker compose ps            # 헬스체크 확인
```

| 서비스 | 포트 | 비고 |
|---|---|---|
| Neo4j | 7474 (UI), 7687 (bolt) | 그래프 + 벡터 |
| Ollama | 11434 | 로컬 LLM 서빙 |
| FastAPI | 8000 | `/api/draft`, `/api/upload`, `/api/validate` |
| Streamlit | 8501 | MVP UI |

Ollama 모델은 최초 1회 받아야 한다:
```bash
docker exec lg_ollama ollama pull qwen2.5:32b
docker exec lg_ollama ollama pull bge-m3
```

Neo4j 컨테이너가 뜨면 제약조건과 벡터 인덱스를 1회 초기화한다 (구
PostgreSQL `init-db`를 대체 — DECISIONS D-006):
```bash
uv run python -m src.graph.etl init-schema
```

## 파이프라인 단계

| Step | 모듈 | 산출물 |
|---|---|---|
| 0 | `src.extract.inventory` | `data/interim/file_inventory.parquet` |
| 1 | `ontology/` | `v1_2_schema.json`, `models.py`, `axioms.py` |
| 2 | `src.extract.form_classifier` | 양식 자동 분류 |
| 3 | `src.transform.schema_mapper` | `ontology/mapping_rules/*.yaml` |
| 4 | `src.transform.entity_resolution` | `data/processed/entities/*.parquet` |
| 6 | `src.graph.etl` | Neo4j 그래프 + 벡터 인덱스 |
| 7 | `src.embed` | BGE-M3 임베딩, Neo4j hybrid search |
| 8 | `src.eval` | 평가 CLI |
| 10 | `src.agent`, `src.api`, `src.ui` | LangGraph 에이전트 + FastAPI + Streamlit |

OG-RAG hypergraph(`src.og_rag`)는 v2 항목 — DECISIONS D-003 참조.

## 테스트

```bash
uv run pytest
```

실제 LG 엑셀 데이터가 없어 합성 fixture(`tests/fixtures/`)로 검증한다.
실데이터·로컬 LLM 의존 작업은 코드에 `# TODO(real-data)`, `D-003`으로 표기되어 있다.
