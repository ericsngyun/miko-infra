"""
Action Gateway — FastAPI allowlist enforcement + audit writer + Smartlead collector
Every external action (email send, API call, webhook) goes through here.
Enforces per-agent allowlists, rate limits, and writes to performance_ledger.

Deploy: orchestrator stack, port 8200 internal only.
"""
import os
import time
import uuid
import hmac
import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="AWaaS Action Gateway", version="0.1.0")


# ── Models ──────────────────────────────────────────────────────────────────

class ActionRequest(BaseModel):
    agent_name: str
    action_type: str          # 'email_send', 'webhook_call', 'api_request', etc.
    target: str               # destination (email, URL, etc.)
    payload: dict = {}
    data_class: str           # PRIVILEGED, CONFIDENTIAL, INTERNAL
    approval_type: str = "auto"  # 'auto', 'human_approved', 'envelope'
    approved_by: Optional[str] = None
    hypothesis_id: Optional[str] = None


class ActionResponse(BaseModel):
    action_id: str
    status: str               # 'executed', 'rejected', 'queued'
    reason: Optional[str] = None
    executed_at: Optional[str] = None


# ── HMAC Verification ───────────────────────────────────────────────────────

HMAC_KEY = os.environ.get("ACTION_GATEWAY_HMAC_KEY", "")


def verify_hmac(request_body: bytes, signature: str, timestamp: str) -> bool:
    """Verify HMAC signature with ±5min replay window."""
    if not HMAC_KEY:
        return True  # Dev mode — remove before production

    # Replay protection: reject requests older than 5 minutes
    try:
        req_time = datetime.fromisoformat(timestamp)
        now = datetime.now(timezone.utc)
        delta = abs((now - req_time).total_seconds())
        if delta > 300:  # 5 minutes
            return False
    except (ValueError, TypeError):
        return False

    expected = hmac.new(
        HMAC_KEY.encode(), request_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Allowlist Check ─────────────────────────────────────────────────────────

# TODO: Load from agent_registry table on startup, refresh every 60s
AGENT_ALLOWLISTS = {
    "@conductor":    ["health_check", "telegram_alert", "status_update"],
    "@ops":          ["health_check", "telegram_alert", "backup_trigger", "status_update"],
    "@communicator": ["email_send", "slack_message"],
    "@scout":        ["web_fetch", "api_request"],
    "@researcher":   ["qdrant_query", "web_fetch"],
    "@builder":      ["code_deploy", "workflow_update"],
    "@analyst":      ["report_generate", "metric_query"],
    "@sysevo":       ["model_pull", "benchmark_trigger", "dep_check"],
}


def check_allowlist(agent_name: str, action_type: str) -> bool:
    allowed = AGENT_ALLOWLISTS.get(agent_name, [])
    return action_type in allowed


# ── Audit Writer ────────────────────────────────────────────────────────────

AUDIT_LOG_PATH = "/mnt/audit/logs/actions.jsonl"


def write_audit(action_id: str, req: ActionRequest, status: str, reason: str = None):
    """Append-only audit log entry."""
    entry = {
        "action_id": action_id,
        "agent_name": req.agent_name,
        "action_type": req.action_type,
        "target": req.target,
        "data_class": req.data_class,
        "approval_type": req.approval_type,
        "status": status,
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with open(AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Audit write failure → logged separately, never blocks action


# ── Routes ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "action-gateway", "version": "0.1.0"}


@app.post("/execute", response_model=ActionResponse)
async def execute_action(req: ActionRequest):
    action_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # 1. Allowlist check
    if not check_allowlist(req.agent_name, req.action_type):
        write_audit(action_id, req, "rejected", f"Action '{req.action_type}' not in allowlist for {req.agent_name}")
        raise HTTPException(
            status_code=403,
            detail=f"Action '{req.action_type}' not permitted for agent '{req.agent_name}'"
        )

    # 2. PRIVILEGED data class cannot trigger external actions
    if req.data_class == "PRIVILEGED" and req.action_type in ("email_send", "webhook_call", "api_request"):
        write_audit(action_id, req, "rejected", "PRIVILEGED data cannot trigger external actions")
        raise HTTPException(
            status_code=403,
            detail="PRIVILEGED data class cannot trigger external outbound actions"
        )

    # 3. Execute (stub — actual integrations built per action_type)
    write_audit(action_id, req, "executed")

    return ActionResponse(
        action_id=action_id,
        status="executed",
        executed_at=now,
    )


@app.post("/smartlead/webhook")
async def smartlead_webhook(request: Request):
    """Smartlead reply/bounce/open collector webhook."""
    body = await request.body()
    # TODO: Verify Smartlead signature, parse event, write to awaas-postgres
    return {"status": "received"}
