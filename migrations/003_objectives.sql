-- ============================================================================
-- 003_objectives.sql — Sprint and quarterly objectives
-- Target: master-postgres
-- ============================================================================

CREATE TABLE IF NOT EXISTS objectives (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    description TEXT NOT NULL,
    level TEXT NOT NULL CHECK (level IN ('sprint_30d','quarterly','annual')),
    owner TEXT NOT NULL,          -- 'eric', 'david', 'joint'
    success_condition TEXT NOT NULL,
    priority_weight NUMERIC(3,2) NOT NULL DEFAULT 0.5,
    risk_weight NUMERIC(3,2) NOT NULL DEFAULT 0.3,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','achieved','failed','deferred')),
    valid_until TIMESTAMPTZ NOT NULL,
    achieved_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_obj_active ON objectives (status, valid_until)
    WHERE status = 'active';

CREATE TRIGGER trg_obj_updated BEFORE UPDATE ON objectives
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
