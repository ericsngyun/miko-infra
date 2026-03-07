-- ============================================================================
-- 008_sysevo_schemas.sql — @sysevo tracking tables
-- model_registry, dependency_registry, feature_backlog, tool_discoveries, hardware_horizon
-- Target: master-postgres
-- ============================================================================

CREATE TABLE IF NOT EXISTS model_registry (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_tag TEXT NOT NULL,       -- exact ollama tag e.g. 'qwen3:30b-a3b'
    provider TEXT NOT NULL DEFAULT 'ollama',
    classification TEXT CHECK (classification IN (
        'CORE_REPLACEMENT','CAPABILITY_EXPANSION','INCREMENTAL','UNRELATED')),
    status TEXT NOT NULL DEFAULT 'detected'
        CHECK (status IN ('detected','staging','benchmarking','shadow','approved','promoted','rejected','retired')),
    benchmark_results JSONB,
    golden_dataset_scores JSONB,
    verdict TEXT CHECK (verdict IN ('MAJOR_UPGRADE','MINOR_IMPROVEMENT','NO_CHANGE','REGRESSION')),
    promoted_at TIMESTAMPTZ,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (model_name, model_tag)
);

CREATE TABLE IF NOT EXISTS dependency_registry (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    package_name TEXT NOT NULL,
    package_type TEXT NOT NULL CHECK (package_type IN ('pip','npm','docker','apt','cargo')),
    current_version TEXT NOT NULL,
    latest_version TEXT,
    cve_score NUMERIC(3,1),        -- highest active CVE score
    update_class TEXT CHECK (update_class IN (
        'CRITICAL_SECURITY','SAFE_UPDATE','BREAKING_CHANGE','NEVER_AUTO')),
    auto_update_eligible BOOLEAN NOT NULL DEFAULT false,
    last_checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated_at TIMESTAMPTZ,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feature_backlog (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    title TEXT NOT NULL,
    source TEXT NOT NULL,           -- 'sysevo_model_track', 'sysevo_feature_track', 'admin', etc.
    source_url TEXT,
    priority_score NUMERIC(5,2),   -- (gain * time_sensitivity) / risk
    gain_description TEXT,
    risk_description TEXT,
    status TEXT NOT NULL DEFAULT 'proposed'
        CHECK (status IN ('proposed','approved','in_sprint','completed','rejected','deferred')),
    sprint_target TEXT,
    decided_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tool_discoveries (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tool_name TEXT NOT NULL,
    source TEXT NOT NULL,           -- 'github_trending', 'hn_show', 'x_mention'
    source_url TEXT,
    replaces TEXT,                  -- current stack equivalent if any
    verdict TEXT CHECK (verdict IN ('ADOPT','MONITOR','IGNORE')),
    reasoning TEXT,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS hardware_horizon (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    hardware_name TEXT NOT NULL,
    category TEXT CHECK (category IN ('apu','gpu','mini_pc','storage','networking')),
    specs JSONB,
    roi_analysis JSONB,            -- throughput_gain_pct, migration_cost, payback_months
    recommendation TEXT CHECK (recommendation IN ('buy','wait','skip')),
    last_evaluated TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_model_status ON model_registry (status);
CREATE INDEX IF NOT EXISTS idx_dep_class ON dependency_registry (update_class);
CREATE INDEX IF NOT EXISTS idx_feature_status ON feature_backlog (status, priority_score DESC);

CREATE TRIGGER trg_model_updated BEFORE UPDATE ON model_registry
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER trg_feature_updated BEFORE UPDATE ON feature_backlog
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
