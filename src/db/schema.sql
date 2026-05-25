-- Step 5 — Postgres-only extensions on top of the ORM schema.
--
-- The ORM in src/db/models.py creates every table without the vector / trigram
-- columns and indexes; this file adds them. Run order:
--     1. ORM: ``init_db(engine)`` -> CREATE TABLE ... (portable)
--     2. This file (Postgres only) -> CREATE EXTENSION + ADD COLUMN vector + indexes
--
-- Run via `python -m src.cli db init` against a Postgres URL.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Embedding columns. Dimensionality 1024 matches BGE-M3.
ALTER TABLE parts
    ADD COLUMN IF NOT EXISTS description_embedding vector(1024);

ALTER TABLE change_events
    ADD COLUMN IF NOT EXISTS change_point_embedding  vector(1024),
    ADD COLUMN IF NOT EXISTS change_reason_embedding vector(1024);

-- Native vector indexes (HNSW, cosine).
CREATE INDEX IF NOT EXISTS idx_parts_description_embed
    ON parts USING hnsw (description_embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_events_change_point_embed
    ON change_events USING hnsw (change_point_embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS idx_events_change_reason_embed
    ON change_events USING hnsw (change_reason_embedding vector_cosine_ops);

-- Trigram index for lexical similarity (hybrid search lex side).
CREATE INDEX IF NOT EXISTS idx_events_change_point_trgm
    ON change_events USING gin (change_point gin_trgm_ops);
