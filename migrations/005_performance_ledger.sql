-- ============================================================================
-- 005_performance_ledger.sql — One row per Action Gateway invocation + outcome
-- Target: master-postgres
-- ============================================================================

CREATE TABLE IF NOT EXISTS performance_ledger (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agent_name TEXT NOT NULL,
    action_type TEXT NOT NULL,     -- 'smartlead_send', 'clio_update', 'slack_message', etc.
    target TEXT NOT NULL,          -- endpoint or resource acted upon
    project_id UUID REFERENCES project_registry(id),
    tier TEXT NOT NULL,            -- 'T0','T1','T2a','T2b','T3'
    request_payload_hash TEXT,     -- SHA256 of payload (not payload itself for PRIVILEGED)
    response_status INTEGER,
    response_time_ms INTEGER,
    outcome TEXT CHECK (outcome IN ('success','failure','rejected','timeout','rate_limited')),
    rejection_reason TEXT,         -- populated if outcome='rejected'
    audit_hmac TEXT,               -- HMAC signature of the log entry for tamper detection
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_perf_ts ON performance_ledger (ts DESC);
CREATE INDEX IF NOT EXISTS idx_perf_agent ON performance_ledger (agent_name, ts DESC);
CREATE INDEX IF NOT EXISTS idx_perf_outcome ON performance_ledger (outcome, ts DESC);
CREATE INDEX IF NOT EXISTS idx_perf_project ON performance_ledger (project_id, ts DESC);
