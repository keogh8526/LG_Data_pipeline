# 의사결정 로그 (DECISIONS)

양식이 진화하고 데이터가 추가될 때마다 "왜 이렇게 했나"를 기록한다.

## D-001: 통합 v1.2를 정답 schema로 채택
- 날짜: 2026-05-22
- 컨텍스트: 양식 4종(20col/56col/96col/통합 v1.2) 공존, v1.2가 최신.
- 결정: v1.2를 ontology의 진실(answer schema)로 고정. 나머지 양식은 모두 v1.2로 매핑.
- 대안: 평균 합집합 양식 신규 작성 (기각 — 합의 비용 과다).
- 영향: Step 1~10 전반.
- 재검토 조건: v1.3 출현 시.

## D-002: 합성 fixture 기반 골격 구축 (실데이터 도착 전)
- 날짜: 2026-05-22
- 컨텍스트: 실제 LG 엑셀 9개 샘플이 아직 업로드되지 않음.
- 결정: `tests/fixtures/`의 합성 엑셀로 결정론적 코드 경로를 검증하며 Step 0~10
  골격을 먼저 구축. 실데이터 의존 부분은 `# TODO(real-data)`로 표기.
- 영향: Step 1(ontology)·Step 3(mapping)은 잠정(provisional) 상태. 실데이터의
  R1~R5 멀티헤더 도착 시 보강 필요.
- 재검토 조건: 실데이터 업로드 시.

## D-003: Step 9~10(OG-RAG/Agent)은 인터페이스 골격만
- 날짜: 2026-05-22
- 컨텍스트: 실데이터·LLM API 키 부재 상태에서 RAG/Agent 동작 로직을 완성하면
  대부분 재작성될 dead code가 됨.
- 결정: 패키지 구조 + 함수 시그니처 + docstring 수준의 골격만 구현. 동작 로직은
  실데이터·LLM 키 확보 후 구현.
- 대안: 풀 구현 (기각 — 검증 불가, 재작성 비용).
- 영향: Step 9~10.
- 재검토 조건: 실데이터 + LLM API 키 확보 시.

## D-004: 무거운 ML 의존성은 optional + lazy 로딩
- 날짜: 2026-05-22
- 컨텍스트: `sentence-transformers`(torch ~2GB), bge-m3 가중치(~2.3GB)는
  런타임 다운로드. `outlines`도 torch 의존이 무거움.
- 결정: `sentence-transformers`/`openai`는 `pyproject.toml`의 optional extra로
  분리. 임베딩 모델 로드는 lazy + `ENABLE_EMBEDDING` 플래그로 게이트. `outlines`는
  미도입하고 OpenAI Structured Outputs(순수 HTTP) 인터페이스만 사용.
- 영향: Step 7, Step 10.
- 재검토 조건: 운영 환경 임베딩 모델 확정 시.
