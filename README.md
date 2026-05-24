# LG_Data_pipeline

LG 가전 신규 모델 개발 시, 기존 양산 모델(Base) 대비 부품 변경을 추적하는
개발부품 마스터/BOM 문서를 표준화·DB화·검색 가능하게 만드는 데이터 파이프라인.

## 개요 (v2 — 1개월 MVP, 전처리 중심)

양식 4종(20col / 56col / 96col / v1.2)으로 흩어진 개발부품 마스터를 **96col
정답 schema**로 매핑·정규화·검증한 뒤 **PostgreSQL + pgvector**에 batch로
적재한다. 사용자가 손으로 만든 결과(`data/golden/`)를 ground truth로 두고,
자동 결과와 매 dry-run에 diff하여 자동화 정확도를 추적한다.

아키텍처 결정 배경은 [`DECISIONS.md`](./DECISIONS.md) D-007 참조.

## MVP 기술 스택

| 계층 | 기술 |
|---|---|
| Storage | PostgreSQL 16 + pgvector + pg_trgm (단독) |
| LLM | Ollama (Qwen 2.5) + BGE-M3 임베딩 (1024 dim) |
| Schema/Rules | YAML config + Pydantic + axioms (코드) |
| CLI | typer (`python -m src.cli`) |
| 데이터 처리 | pandas + pyarrow + openpyxl + pandera + rapidfuzz |

Backend(FastAPI)·Frontend(Streamlit)·Agent(LangGraph)는 다음 phase.

## 요구 사항

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)
- Docker / Docker Compose

## 설치

```bash
uv sync --extra dev        # 핵심 + 개발 의존성
uv sync --extra embed      # BGE-M3 sentence-transformers fallback (~2GB)
cp .env.example .env
```

## 로컬 구동

```bash
docker compose up -d                            # PostgreSQL + Ollama
docker exec lg_ollama ollama pull qwen2.5:32b   # 1회
docker exec lg_ollama ollama pull bge-m3
```

## 디렉토리 구조

```
config/
  form_signatures.yaml         # 양식 분류 시그니처 (Step 1)
  axioms.yaml                  # 부품번호·모델코드·alias 등 (Step 2)
  mapping_rules/v1_2.yaml      # 96col → v1.2 forward-compat (placeholder)
data/
  raw/        golden/          # 원본 / 사용자 손작업 (ground truth)
  interim/    processed/       # 중간 / 최종 산출물
  quarantine/ reports/         # 격리 / markdown 리포트
src/
  ontology/   schema.py, schema.json, axioms.py
  preprocess/ inventory, classify, diff (Step 0~1 완료, 나머지는 Step 3~4)
  utils/      logging, paths, audit
  cli.py
tests/
```

## 사용 가능한 명령 (Step 0~2)

| 명령 | 단계 | 설명 |
|---|---|---|
| `uv run python -m src.cli inventory` | Step 0 | `data/raw/` 스캔 → `data/interim/file_inventory.parquet` |
| `uv run python -m src.cli classify <path> [--all]` | Step 1 | 양식 분류 (파일 또는 디렉토리) |
| `uv run python -m src.cli schema-export` | Step 2 | 96col 정답 schema → JSON Schema 출력 |

## 파이프라인 단계 (v2)

| Step | 산출물 | 상태 |
|---|---|---|
| 0 | 환경, 인벤토리, golden 폴더 명세 | ✅ |
| 1 | `config/form_signatures.yaml` + 결정론적 분류기 | ✅ |
| 2 | 96col Pydantic schema + axioms (config-driven) | ✅ |
| 3 | 양식별 매핑 + 정규화 + Entity Resolution | 예정 |
| 4 | 15지표 검증 + Quarantine + Golden diff + dry-run/commit/rollback | 예정 |
| 5 | PostgreSQL + pgvector 적재 (5 테이블 + run_id 배치) | 예정 |

## 테스트

```bash
uv run pytest
```

실데이터 부재 상태에서는 `tests/fixtures/`의 합성 엑셀로 결정론적 코드 경로만
검증한다 (DECISIONS D-002).
