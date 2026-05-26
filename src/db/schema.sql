-- ============================================================
-- v2.0 (D-011 간소화 후) — PostgreSQL schema.
--   Core 13 cols + extra_fields(JSONB) + narrative_emb 단일 벡터.
-- ============================================================
--
-- D-011 변경 사항:
--   - multi-vector 5개 → narrative_emb 1개로 축소
--   - HNSW 인덱스 5개 → 1개
--   - trigram GIN 인덱스 3개 → narrative_text 1개
--   - payload JSONB GIN 제거 (extra_fields는 일반 JSONB로만 보존, 검색 X)
--
-- ORM(src/db/models.py)이 portable한 CREATE TABLE을 먼저 만들고, 본 파일은
-- Postgres 전용 확장과 vector / trigram 컬럼·인덱스만 추가한다. SQLite 테스트
-- 는 본 파일을 skip.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── change_events: narrative_emb 단일 벡터 컬럼 ──────────────
ALTER TABLE change_events
    ADD COLUMN IF NOT EXISTS narrative_emb vector(1024);

-- HNSW 벡터 인덱스 (m=16, ef_construction=200 -- pgvector 0.5+)
CREATE INDEX IF NOT EXISTS idx_ce_narrative_emb
    ON change_events USING hnsw (narrative_emb vector_cosine_ops);

-- Trigram 인덱스 (lexical / ILIKE 가속)
CREATE INDEX IF NOT EXISTS idx_ce_narrative_trgm
    ON change_events USING gin (narrative_text gin_trgm_ops);

-- D-011 Phase C: parts.description_embedding 컬럼 제거 (BOM Agent가 미사용).
