-- Migration 010: Create workflow_runs table
-- Tracks execution of workflows (demand drafts, outreach, scout, digest)
-- Created: 2026-03-15

CREATE TABLE IF NOT EXISTS workflow_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_type VARCHAR(50) NOT NULL,  -- e.g. "demand_draft", "outreach", "scout", "digest"
    status VARCHAR(20) NOT NULL,     -- "pending", "running", "completed", "failed"
    agent_id VARCHAR(50) NOT NULL,
    org_id VARCHAR(50),
    input_hash VARCHAR(64),
    model_used VARCHAR(50),
    duration_ms INTEGER,
    token_count INTEGER,
    output_summary TEXT,
    error TEXT,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index on status for filtering active runs
CREATE INDEX idx_workflow_runs_status ON workflow_runs(status);

-- Index on agent_id for agent-specific queries
CREATE INDEX idx_workflow_runs_agent ON workflow_runs(agent_id);

-- Index on run_type for filtering by workflow type
CREATE INDEX idx_workflow_runs_type ON workflow_runs(run_type);

-- Index on created_at for time-based queries
CREATE INDEX idx_workflow_runs_created ON workflow_runs(created_at DESC);

-- Composite index for common query pattern (agent + status)
CREATE INDEX idx_workflow_runs_agent_status ON workflow_runs(agent_id, status);

-- Update trigger for updated_at
CREATE OR REPLACE FUNCTION update_workflow_runs_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_workflow_runs_updated_at
    BEFORE UPDATE ON workflow_runs
    FOR EACH ROW
    EXECUTE FUNCTION update_workflow_runs_timestamp();

COMMENT ON TABLE workflow_runs IS 'Tracks execution of agentic workflows (demand drafts, outreach, research)';
COMMENT ON COLUMN workflow_runs.input_hash IS 'SHA-256 hash of input for deduplication';
COMMENT ON COLUMN workflow_runs.metadata IS 'Flexible JSON storage for workflow-specific data';
