-- ============================================================================
-- 001_core_tables.sql — project_registry, health_log, inference_log, spend_log
-- Target: master-postgres (orchestrator stack)
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS project_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL UNIQUE,
    data_class TEXT NOT NULL CHECK (data_class IN ('PRIVILEGED','CONFIDENTIAL','INTERNAL')),
    priority INTEGER NOT NULL DEFAULT 5,
    daily_spend_cap_usd NUMERIC(10,2) NOT NULL DEFAULT 50.00,
    routing_policy TEXT NOT NULL DEFAULT 'local_preferred',
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS health_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    project_id UUID REFERENCES project_registry(id),
    service TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('healthy','degraded','down','unknown')),
    response_time_ms INTEGER,
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS inference_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id UUID NOT NULL DEFAULT gen_random_uuid(),
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    project_id UUID REFERENCES project_registry(id),
    data_class TEXT NOT NULL,
    routing TEXT NOT NULL CHECK (routing IN ('local','cloud_anthropic','cloud_openai','cloud_gemini','rejected')),
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    status TEXT NOT NULL CHECK (status IN ('success','error','timeout','policy_violation')),
    error_detail TEXT,
    audit_entry_ts TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS spend_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    project_id UUID REFERENCES project_registry(id),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd NUMERIC(10,6) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_health_ts ON health_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_health_svc ON health_log (service, ts DESC);
CREATE INDEX IF NOT EXISTS idx_infer_ts ON inference_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_infer_proj ON inference_log (project_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_infer_dc ON inference_log (data_class, ts DESC);
CREATE INDEX IF NOT EXISTS idx_spend_ts ON spend_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_spend_proj ON spend_log (project_id, ts DESC);

INSERT INTO project_registry (name, data_class, priority, daily_spend_cap_usd, routing_policy) VALUES
    ('pleadly',        'PRIVILEGED',    1, 30.00, 'local_only'),
    ('awaas_services', 'CONFIDENTIAL',  3, 50.00, 'local_preferred'),
    ('trading',        'INTERNAL',      7, 10.00, 'cloud_allowed'),
    ('orchestrator',   'CONFIDENTIAL',  2, 30.00, 'local_preferred')
ON CONFLICT (name) DO NOTHING;

CREATE OR REPLACE FUNCTION update_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END; $$ LANGUAGE plpgsql;

CREATE TRIGGER trg_project_updated BEFORE UPDATE ON project_registry
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
