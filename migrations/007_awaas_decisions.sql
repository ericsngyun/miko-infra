-- ============================================================================
-- 007_awaas_decisions.sql — Governance log for all Tier 2+ decisions
-- Target: master-postgres
-- ============================================================================

CREATE TABLE IF NOT EXISTS awaas_decisions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    decision_type TEXT NOT NULL DEFAULT 'operational'
        CHECK (decision_type IN ('operational','tier_promotion','spend_raise',
            'model_promotion','security_incident','moat_session','go_no_go',
            'workflow_expansion','dependency_update','rollback')),
    decision TEXT NOT NULL,
    rationale TEXT,
    decided_by TEXT NOT NULL,      -- 'eric', 'david', 'eric+david', 'conductor'
    decided_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    requires_both_admins BOOLEAN NOT NULL DEFAULT false,
    second_admin_confirmed BOOLEAN DEFAULT false,
    second_admin_confirmed_at TIMESTAMPTZ,
    metadata JSONB,                -- structured data specific to decision type
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dec_type ON awaas_decisions (decision_type, decided_at DESC);
CREATE INDEX IF NOT EXISTS idx_dec_by ON awaas_decisions (decided_by, decided_at DESC);
