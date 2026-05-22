# LG_Data_pipeline

LG 가전 신규 모델 개발 시, 기존 양산 모델(Base) 대비 부품 변경을 추적하는
개발부품 마스터/BOM 문서를 표준화·DB화·검색 가능하게 만드는 데이터 파이프라인.

## 개요

양식 4종(20col / 56col / 96col / 통합 v1.2)으로 흩어진 개발부품 마스터를 정답
ontology(통합 v1.2)로 매핑하고, PostgreSQL(정형) + Neo4j(그래프) + Qdrant(벡터)에
적재하여 OG-RAG 기반 검색·Agent의 기반을 만든다.

## 요구 사항

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (패키지 매니저)
- Docker / Docker Compose (로컬 DB 구동)

## 설치

```bash
uv sync --extra dev          # 핵심 + 개발 의존성
uv sync --extra embed        # 임베딩(sentence-transformers) 추가 시
uv sync --extra llm          # LLM(openai) 추가 시
cp .env.example .env         # 환경 변수 설정
```

## 로컬 DB 구동

```bash
docker compose up -d         # PostgreSQL + Neo4j + Qdrant
docker compose ps            # 헬스체크 확인 (healthy)
docker compose down          # 중지 (-v 추가 시 볼륨 삭제)
```

| 서비스 | 포트 | 비고 |
|---|---|---|
| PostgreSQL | 5432 | 정형 데이터 (SSoT) |
| Neo4j | 7474 (UI), 7687 (bolt) | 그래프 |
| Qdrant | 6333 (REST), 6334 (gRPC) | 벡터 |

## 파이프라인 단계

| Step | 모듈 | 산출물 |
|---|---|---|
| 0 | `src.extract.inventory` | `data/interim/file_inventory.parquet` |
| 1 | `ontology/` | `v1_2_schema.json`, `models.py`, `axioms.py` |
| 2 | `src.extract.form_classifier` | 양식 자동 분류 |
| 3 | `src.transform.schema_mapper` | `ontology/mapping_rules/*.yaml` |
| 4 | `src.transform.entity_resolution` | `data/processed/entities/*.parquet` |
| 5 | `src.load` | PostgreSQL 7개 테이블 |
| 6 | `src.graph.etl` | Neo4j 그래프 |
| 7 | `src.embed` | Qdrant collection |
| 8 | `src.eval` | 평가 CLI |
| 9~10 | `src.og_rag`, `src.agent` | RAG 레이어 (인터페이스 골격) |

## 새 파일 처리 절차

```bash
# 1. data/raw/ 에 파일 복사 후
uv run python -m src.extract.inventory
uv run python -m src.extract.form_classifier classify <file>
uv run python -m src.transform.schema_mapper apply <file>
uv run python -m src.load load --file <file>
uv run python -m src.graph.etl sync --incremental
uv run python -m src.embed sync --incremental
uv run python -m src.eval all
```

## 테스트

```bash
uv run pytest
```

실제 LG 엑셀 데이터가 없어 합성 fixture(`tests/fixtures/`)로 검증한다.
실데이터 의존 작업은 코드에 `# TODO(real-data)` 로 표기되어 있다.

자세한 의사결정 이력은 [`DECISIONS.md`](./DECISIONS.md) 참조.
