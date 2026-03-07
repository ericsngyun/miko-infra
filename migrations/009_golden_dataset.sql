-- ============================================================================
-- 009_golden_dataset.sql — Pleadly evaluation corpus
-- Intake cases, medical bundles, demand letters for regression testing
-- Target: master-postgres
-- ============================================================================

CREATE TABLE IF NOT EXISTS golden_dataset (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_type TEXT NOT NULL,        -- 'auto_pi', 'slip_fall', 'med_mal', 'dog_bite', etc.
    jurisdiction TEXT NOT NULL,     -- 'CA', 'TX', 'FL', 'NY'
    task_type TEXT NOT NULL CHECK (task_type IN (
        'demand_draft','sol_calculation','document_classification',
        'medical_chronology','liability_analysis','damages_summary')),
    input_payload JSONB NOT NULL,   -- sanitized test input (no real PII)
    expected_output JSONB NOT NULL, -- verified correct output
    evaluation_criteria JSONB NOT NULL,  -- scoring rubric
    sol_answer_days INTEGER,        -- for SOL tasks: correct answer in days
    difficulty TEXT NOT NULL DEFAULT 'standard'
        CHECK (difficulty IN ('simple','standard','complex','edge_case')),
    added_by TEXT NOT NULL DEFAULT 'eric',
    active BOOLEAN NOT NULL DEFAULT true,
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Benchmark run results against golden dataset
CREATE TABLE IF NOT EXISTS golden_dataset_results (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id UUID NOT NULL DEFAULT gen_random_uuid(),
    dataset_id BIGINT NOT NULL REFERENCES golden_dataset(id),
    model_name TEXT NOT NULL,
    model_tag TEXT NOT NULL,
    score NUMERIC(5,2),            -- 0-100
    sol_correct BOOLEAN,           -- for SOL tasks: binary pass/fail
    latency_ms INTEGER,
    output_payload JSONB,
    evaluation_details JSONB,      -- per-criterion scores
    run_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_golden_type ON golden_dataset (task_type, case_type);
CREATE INDEX IF NOT EXISTS idx_golden_results_run ON golden_dataset_results (run_id);
CREATE INDEX IF NOT EXISTS idx_golden_results_model ON golden_dataset_results (model_name, model_tag, run_at DESC);

CREATE TRIGGER trg_golden_updated BEFORE UPDATE ON golden_dataset
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
