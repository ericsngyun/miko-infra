# Migration 010-011 Status Report

## Created

### 010_workflow_runs.sql ✅
**Status**: Successfully created and applied to master-postgres

**Table**: `workflow_runs`
- Tracks execution of agentic workflows (demand drafts, outreach, scout, digest)
- 14 columns with proper indexes and auto-update trigger
- Includes deduplication via input_hash
- JSONB metadata for flexible workflow-specific data

**Schema verified**:
```sql
\d workflow_runs
-- All columns present as specified
-- 5 indexes created (status, agent, type, created, agent+status composite)
-- Updated_at trigger active
```

## Pre-existing

### 011_approval_queue.sql ⚠️
**Status**: Table already exists with different schema

**Current schema** (pre-existing):
```sql
CREATE TABLE approval_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    project_id INTEGER REFERENCES projects(id),
    agent TEXT NOT NULL,
    action_type TEXT NOT NULL,
    payload_summary TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    decided_by TEXT,
    decided_at TIMESTAMPTZ
);
```

**Requested schema** (migration 011):
```sql
CREATE TABLE approval_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id VARCHAR(50) NOT NULL,
    action_type VARCHAR(50) NOT NULL,
    payload JSONB NOT NULL,
    requested_at TIMESTAMPTZ DEFAULT NOW(),
    timeout_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '4 hours'),
    resolved_by VARCHAR(50),
    resolution VARCHAR(20),
    resolved_at TIMESTAMPTZ,
    notes TEXT
);
```

**Key differences**:
- Existing: Uses `agent` (text), requested uses `agent_id` (varchar(50))
- Existing: Uses `payload_summary` (text), requested uses `payload` (jsonb)
- Existing: Uses `status` ('pending'/'approved'/'rejected'), requested uses `resolution` ('approved'/'rejected'/'timeout')
- Existing: Has `project_id` FK, requested doesn't
- Existing: Uses `decided_by`/`decided_at`, requested uses `resolved_by`/`resolved_at`
- Requested: Has `timeout_at` auto-timeout mechanism and `notes` field

**Decision**: Per directive "do not touch any existing tables or migrations", the existing approval_queue table was left unchanged. Migration 011 file created but not applied.

**Action if schema change needed**:
- Create 012_alter_approval_queue.sql to migrate existing table to new schema
- Or use existing schema and update application code to match
- Or rename existing to `legacy_approval_queue` and create new table

## Verification

```bash
# Verify both tables exist
docker exec master-postgres psql -U awaas_master awaas_master -c '\dt' | grep -E 'workflow_runs|approval_queue'

# Output:
# public | approval_queue       | table | awaas_master
# public | workflow_runs        | table | awaas_master
```

## Next Steps

1. ✅ `workflow_runs` ready for use immediately
2. ⚠️ Decide on `approval_queue` schema reconciliation:
   - Option A: Alter existing table (breaking change)
   - Option B: Use existing schema (update app code)
   - Option C: Create new table with different name (e.g. `agent_approvals`)

## Files Created

- `~/awaas/migrations/010_workflow_runs.sql` - Applied ✅
- `~/awaas/migrations/011_approval_queue.sql` - Created but not applied ⚠️
