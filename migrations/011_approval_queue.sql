-- Migration 011: Create approval_queue table
-- Manages approval workflow for agent actions requiring human oversight
-- Created: 2026-03-15

CREATE TABLE IF NOT EXISTS approval_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(50) NOT NULL,
    action_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timeout_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '4 hours'),
    resolved_by VARCHAR(50),
    resolution VARCHAR(20),  -- "approved", "rejected", "timeout"
    resolved_at TIMESTAMPTZ,
    notes TEXT
);

-- Index on agent_id for filtering by agent
CREATE INDEX idx_approval_queue_agent ON approval_queue(agent_id);

-- Index on resolution for filtering pending/resolved requests
CREATE INDEX idx_approval_queue_resolution ON approval_queue(resolution);

-- Index on requested_at for time-ordered queries
CREATE INDEX idx_approval_queue_requested ON approval_queue(requested_at DESC);

-- Index on timeout_at for finding expired requests
CREATE INDEX idx_approval_queue_timeout ON approval_queue(timeout_at);

-- Composite index for pending items (unresolved, not timed out)
CREATE INDEX idx_approval_queue_pending ON approval_queue(resolution, timeout_at) 
    WHERE resolution IS NULL;

-- Check constraint: resolution must be valid value if set
ALTER TABLE approval_queue 
    ADD CONSTRAINT chk_approval_resolution 
    CHECK (resolution IS NULL OR resolution IN ('approved', 'rejected', 'timeout'));

-- Check constraint: resolved_at must be set if resolution is set
ALTER TABLE approval_queue 
    ADD CONSTRAINT chk_approval_resolved_at 
    CHECK ((resolution IS NULL AND resolved_at IS NULL) OR 
           (resolution IS NOT NULL AND resolved_at IS NOT NULL));

-- Function to auto-timeout expired approvals
CREATE OR REPLACE FUNCTION timeout_expired_approvals()
RETURNS INTEGER AS $$
DECLARE
    updated_count INTEGER;
BEGIN
    UPDATE approval_queue
    SET resolution = 'timeout',
        resolved_at = NOW()
    WHERE resolution IS NULL
      AND timeout_at < NOW();
    
    GET DIAGNOSTICS updated_count = ROW_COUNT;
    RETURN updated_count;
END;
$$ LANGUAGE plpgsql;

COMMENT ON TABLE approval_queue IS 'Manages approval workflow for agent actions requiring human oversight';
COMMENT ON COLUMN approval_queue.payload IS 'JSON payload containing action details and context';
COMMENT ON COLUMN approval_queue.timeout_at IS 'Automatic timeout threshold (default 4 hours from request)';
COMMENT ON FUNCTION timeout_expired_approvals IS 'Auto-timeout approvals past their deadline - call periodically';
