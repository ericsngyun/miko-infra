-- ============================================================================
-- 004_hypothesis_store.sql — Hypothesis lifecycle: proposed→running→confirmed/refuted
-- Target: master-postgres
-- ============================================================================

CREATE TABLE IF NOT EXISTS hypothesis_store (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hypothesis TEXT NOT NULL,
    domain TEXT NOT NULL,         -- 'pleadly', 'outreach', 'trading', 'infrastructure'
    proposed_by TEXT NOT NULL,    -- agent name or admin
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed','approved','running','confirmed','refuted','abandoned')),
    experiment_design JSONB,     -- what to measure, how, success criteria
    success_criteria TEXT,
    evidence JSONB,              -- accumulated evidence during experiment
    outcome TEXT,                -- final verdict summary
    confidence NUMERIC(3,2),     -- outcome confidence score
    proposed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    approved_by TEXT,            -- admin who approved experiment
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hyp_status ON hypothesis_store (status);
CREATE INDEX IF NOT EXISTS idx_hyp_domain ON hypothesis_store (domain, status);

CREATE TRIGGER trg_hyp_updated BEFORE UPDATE ON hypothesis_store
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
