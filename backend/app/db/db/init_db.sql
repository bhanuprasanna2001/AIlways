-- =============================================================================
-- Database initialization script
-- Runs once when the database volume is initialized (empty).
-- =============================================================================

-- ParadeDB search extension (BM25)
CREATE EXTENSION IF NOT EXISTS pg_search;

-- Vector search (pgvector)
CREATE EXTENSION IF NOT EXISTS vector;

-- Useful for fuzzy matching / indexing helpers (optional)
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- Handy for auth systems: gen_random_uuid(), crypto helpers (optional)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---- Print versions to logs (safe, won’t fail if missing)
DO $$
DECLARE v TEXT;
BEGIN
  SELECT extversion INTO v FROM pg_extension WHERE extname = 'pg_search';
  RAISE NOTICE 'pg_search version: %', COALESCE(v, 'not installed');

  SELECT extversion INTO v FROM pg_extension WHERE extname = 'vector';
  RAISE NOTICE 'vector (pgvector) version: %', COALESCE(v, 'not installed');

  SELECT extversion INTO v FROM pg_extension WHERE extname = 'pg_trgm';
  RAISE NOTICE 'pg_trgm version: %', COALESCE(v, 'not installed');

  SELECT extversion INTO v FROM pg_extension WHERE extname = 'btree_gin';
  RAISE NOTICE 'btree_gin version: %', COALESCE(v, 'not installed');

  SELECT extversion INTO v FROM pg_extension WHERE extname = 'pgcrypto';
  RAISE NOTICE 'pgcrypto version: %', COALESCE(v, 'not installed');
END $$;