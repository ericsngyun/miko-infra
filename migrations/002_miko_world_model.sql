-- ============================================================================
-- 002_miko_world_model.sql — Miko's knowledge store
-- Key-value with confidence, source tracking, and TTL-based freshness
-- Target: master-postgres
-- ============================================================================

CREATE TABLE IF NOT EXISTS miko_world_model (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    field TEXT NOT NULL,
    value JSONB NOT NULL,
    source TEXT NOT NULL,         -- 'manual_benchmark', 'conductor_poll', 'scout_intel', etc.
    confidence NUMERIC(3,2) NOT NULL DEFAULT 0.5 CHECK (confidence BETWEEN 0 AND 1),
    as_of TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    freshness_ttl INTERVAL NOT NULL DEFAULT '7 days',
    superseded_by BIGINT REFERENCES miko_world_model(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Only one active (non-superseded) value per field
CREATE UNIQUE INDEX IF NOT EXISTS idx_wm_active_field
    ON miko_world_model (field) WHERE superseded_by IS NULL;

CREATE INDEX IF NOT EXISTS idx_wm_field ON miko_world_model (field);
CREATE INDEX IF NOT EXISTS idx_wm_stale ON miko_world_model (as_of)
    WHERE superseded_by IS NULL;

-- View: current world state (non-superseded, non-stale)
CREATE OR REPLACE VIEW miko_world_current AS
SELECT id, field, value, source, confidence, as_of, freshness_ttl,
       (NOW() > as_of + freshness_ttl) AS is_stale
FROM miko_world_model
WHERE superseded_by IS NULL;
