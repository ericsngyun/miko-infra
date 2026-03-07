-- ============================================================================
-- 006_agent_registry.sql — Agent configs, permission tiers, allowlists
-- Target: master-postgres
-- ============================================================================

CREATE TABLE IF NOT EXISTS agent_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,     -- '@conductor', '@scout', '@researcher', etc.
    display_name TEXT NOT NULL,
    tier TEXT NOT NULL DEFAULT 'T0'
        CHECK (tier IN ('T0','T1','T2a','T2b','T3')),
    project_id UUID REFERENCES project_registry(id),
    description TEXT,
    config JSONB NOT NULL DEFAULT '{}',  -- agent-specific configuration
    allowlist JSONB NOT NULL DEFAULT '[]',  -- permitted outbound targets
    model_preference TEXT,         -- preferred model string
    max_concurrent INTEGER NOT NULL DEFAULT 1,
    active BOOLEAN NOT NULL DEFAULT true,
    promoted_at TIMESTAMPTZ,       -- last tier promotion timestamp
    promoted_by TEXT,              -- who approved promotion
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_active ON agent_registry (active, tier);

CREATE TRIGGER trg_agent_updated BEFORE UPDATE ON agent_registry
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- Seed the 7-agent fleet
INSERT INTO agent_registry (name, display_name, tier, description) VALUES
    ('@conductor',    'Conductor',      'T0', 'Orchestration: health polls, semaphores, routing'),
    ('@scout',        'Scout',          'T0', 'Market intelligence: source monitoring, signal detection'),
    ('@researcher',   'Researcher',     'T0', 'Deep analysis: corpus building, competitive analysis'),
    ('@builder',      'Builder',        'T0', 'Implementation: code generation, workflow building'),
    ('@ops',          'Ops',            'T0', 'Infrastructure: backup, monitoring, deployment'),
    ('@analyst',      'Analyst',        'T0', 'Performance: metrics, anomaly detection, reporting'),
    ('@communicator', 'Communicator',   'T0', 'Client interaction: outreach drafts, follow-ups')
ON CONFLICT (name) DO NOTHING;
