-- ============================================================
-- v2.0 Step 5 — PostgreSQL schema (preprocessing_v2.md §9)
--   Core 13 cols + JSONB Payload + Multi-Vector (5) per change_events row.
-- ============================================================
--
-- ORM (src/db/models.py)이 portable한 CREATE TABLE을 먼저 만들고, 본 파일은
-- Postgres 전용 확장(uuid-ossp / pgvector / pg_trgm)과 벡터/트라이그램 컬럼·
-- 인덱스만 추가한다. SQLite 백엔드 테스트는 본 파일을 skip한다.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── change_events: Multi-Vector + JSONB payload 컬럼 ──────────────
ALTER TABLE change_events
    ADD COLUMN IF NOT EXISTS narrative_emb     vector(1024),
    ADD COLUMN IF NOT EXISTS change_point_emb  vector(1024),
    ADD COLUMN IF NOT EXISTS change_reason_emb vector(1024),
    ADD COLUMN IF NOT EXISTS drbfm_emb         vector(1024),
    ADD COLUMN IF NOT EXISTS test_plan_emb     vector(1024);

-- HNSW 벡터 인덱스 (m=16, ef_construction=200 — pgvector 0.5+)
CREATE INDEX IF NOT EXISTS idx_ce_narrative_emb
    ON change_events USING hnsw (narrative_emb vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_ce_changept_emb
    ON change_events USING hnsw (change_point_emb vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_ce_reason_emb
    ON change_events USING hnsw (change_reason_emb vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_ce_drbfm_emb
    ON change_events USING hnsw (drbfm_emb vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_ce_test_emb
    ON change_events USING hnsw (test_plan_emb vector_cosine_ops);

-- Payload JSONB GIN (jsonb_path_ops — @> 쿼리 빠름)
CREATE INDEX IF NOT EXISTS idx_ce_payload_gin
    ON change_events USING gin (payload jsonb_path_ops);

-- Trigram 인덱스 (lexical hybrid retrieval)
CREATE INDEX IF NOT EXISTS idx_ce_narrative_trgm
    ON change_events USING gin (narrative_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_ce_changept_trgm
    ON change_events USING gin (change_point gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_ce_reason_trgm
    ON change_events USING gin (change_reason gin_trgm_ops);

-- ── parts: description 벡터 ──
ALTER TABLE parts
    ADD COLUMN IF NOT EXISTS description_embedding vector(1024);
CREATE INDEX IF NOT EXISTS idx_parts_description_embed
    ON parts USING hnsw (description_embedding vector_cosine_ops);
