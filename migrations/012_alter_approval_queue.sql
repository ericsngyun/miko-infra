-- Migration 012: Alter approval_queue table for Telegram approval polling
-- Add timeout_at and notes columns to existing approval_queue table
-- Created: 2026-03-15

-- Add timeout_at column (default 4 hours from now)
ALTER TABLE approval_queue 
  ADD COLUMN IF NOT EXISTS timeout_at TIMESTAMPTZ DEFAULT (NOW() + INTERVAL '4 hours');

-- Add notes column for additional context
ALTER TABLE approval_queue 
  ADD COLUMN IF NOT EXISTS notes TEXT;

-- Create index on timeout_at for efficient polling
CREATE INDEX IF NOT EXISTS idx_approval_queue_timeout_at ON approval_queue(timeout_at);

-- Create index on status + timeout_at for pending approval queries
CREATE INDEX IF NOT EXISTS idx_approval_queue_status_timeout 
  ON approval_queue(status, timeout_at) WHERE status = 'pending';

COMMENT ON COLUMN approval_queue.timeout_at IS 'Auto-expire threshold for pending approvals';
COMMENT ON COLUMN approval_queue.notes IS 'Additional context or resolution notes';
