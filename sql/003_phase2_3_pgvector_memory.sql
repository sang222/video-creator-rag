-- Phase 2.3 pgvector memory retrieval readiness.
--
-- The application keeps SQLite/dev compatibility by mapping embeddings through
-- a portable SQLAlchemy type. In PostgreSQL, install pgvector and keep
-- memory_items.embedding as vector(16) unless EMBEDDING_DIMENSION is changed.
-- If EMBEDDING_DIMENSION is changed, edit vector(16) below to the deployed
-- dimension before applying this SQL.

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE memory_items
  ADD COLUMN IF NOT EXISTS embedded_at timestamptz;

ALTER TABLE memory_items
  ADD COLUMN IF NOT EXISTS embedding vector(16);

CREATE INDEX IF NOT EXISTS ix_memory_items_scope_workspace_family
  ON memory_items(company_id, scope, workspace_id, family);

-- Preferred on pgvector versions that support HNSW.
CREATE INDEX IF NOT EXISTS ix_memory_items_embedding_hnsw
  ON memory_items
  USING hnsw (embedding vector_l2_ops)
  WHERE embedding IS NOT NULL;

-- If HNSW is not available on the deployed pgvector version, use this instead:
-- CREATE INDEX IF NOT EXISTS ix_memory_items_embedding_ivfflat
--   ON memory_items
--   USING ivfflat (embedding vector_l2_ops)
--   WITH (lists = 100)
--   WHERE embedding IS NOT NULL;
