"""
Claude API client for high-quality legal document drafting.

Routing:
  - Demand letters, document analysis  -> claude-sonnet-4-6
  - High-stakes demands (>$500K)       -> claude-opus-4-6
  - Fast/structured tasks              -> claude-haiku-4-5-20251001

Privacy model:
  - Raw PRIVILEGED documents never leave local inference
  - Only synthesized summaries reach Claude API
  - Preserves ABA Opinion 512 compliance argument

Cost logging:
  - Every call writes to master-postgres spend_log + inference_log
  - project_id=1 (pleadly), daily_spend_cap=$30
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from typing import Any

import httpx

logger = logging.getLogger("pleadly.claude")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

MODEL_DRAFTING = os.getenv("CLAUDE_DRAFTING_MODEL", "claude-sonnet-4-6")
MODEL_HEAVY    = os.getenv("CLAUDE_HEAVY_MODEL",    "claude-opus-4-6")
MODEL_FAST     = os.getenv("CLAUDE_FAST_MODEL",     "claude-haiku-4-5-20251001")

OPUS_THRESHOLD = float(os.getenv("CLAUDE_OPUS_THRESHOLD", "500000"))
MASTER_DSN     = os.getenv("MASTER_POSTGRES_DSN", "")

# Anthropic pricing per 1M tokens (March 2026)
PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-6":           {"input": 15.00, "output": 75.00},
    "claude-sonnet-4-6":         {"input":  3.00, "output": 15.00},
    "claude-haiku-4-5-20251001": {"input":  0.80, "output":  4.00},
    "_default":                  {"input":  3.00, "output": 15.00},
}

PROJECT_ID_PLEADLY = 1


class ClaudeError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _calc_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = PRICING.get(model, PRICING["_default"])
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def _select_model(task: str, demand_amount: float | None = None) -> str:
    if task == "fast":
        return MODEL_FAST
    if demand_amount and demand_amount >= OPUS_THRESHOLD:
        logger.info("Escalating to Opus — demand $%.0f >= threshold $%.0f",
                    demand_amount, OPUS_THRESHOLD)
        return MODEL_HEAVY
    return MODEL_DRAFTING


async def _log_to_master(
    request_id: str,
    model: str,
    task: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    status: str,
    cost_usd: float,
) -> None:
    if not MASTER_DSN:
        logger.warning("MASTER_POSTGRES_DSN not set — skipping cost log")
        return
    try:
        import asyncpg
        conn = await asyncpg.connect(MASTER_DSN, timeout=5)
        try:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO spend_log (provider, project_id, cost_usd) VALUES ($1, $2, $3)",
                    f"anthropic/{model}", PROJECT_ID_PLEADLY, cost_usd,
                )
                await conn.execute(
                    """INSERT INTO inference_log
                       (request_id, project_id, data_class, routing, model, tokens, latency_ms, status)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                       ON CONFLICT (request_id) DO NOTHING""",
                    request_id, PROJECT_ID_PLEADLY, "PRIVILEGED",
                    f"claude_api/{task}", model,
                    input_tokens + output_tokens, latency_ms, status,
                )
        finally:
            await conn.close()
        logger.info(
            "Cost logged request_id=%s model=%s in=%d out=%d cost=$%.4f",
            request_id, model, input_tokens, output_tokens, cost_usd,
        )
    except Exception as exc:
        logger.error("Cost logging failed (non-fatal): %s", exc)


async def draft(
    system_prompt: str,
    user_prompt: str,
    task: str = "demand",
    demand_amount: float | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    if not ANTHROPIC_API_KEY:
        raise ClaudeError("ANTHROPIC_API_KEY not configured")

    model = _select_model(task, demand_amount)
    request_id = str(uuid.uuid4())
    start = time.monotonic()

    logger.info("Claude draft request_id=%s task=%s model=%s", request_id, task, model)

    status = "error"
    input_tokens = output_tokens = 0

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0)
        ) as client:
            resp = await client.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                },
            )
            resp.raise_for_status()

        data = resp.json()
        usage         = data.get("usage", {})
        input_tokens  = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        latency_ms    = int((time.monotonic() - start) * 1000)
        cost_usd      = _calc_cost(model, input_tokens, output_tokens)
        status        = "ok"

        logger.info(
            "Claude complete request_id=%s model=%s in=%d out=%d latency=%dms cost=$%.4f",
            request_id, model, input_tokens, output_tokens, latency_ms, cost_usd,
        )

        await _log_to_master(request_id, model, task, input_tokens, output_tokens,
                             latency_ms, status, cost_usd)

        return data["content"][0]["text"]

    except httpx.HTTPStatusError as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        await _log_to_master(request_id, model, task, 0, 0, latency_ms, "http_error", 0.0)
        raise ClaudeError(
            f"Claude API error {exc.response.status_code}: {exc.response.text[:300]}",
            status_code=exc.response.status_code,
        ) from exc
    except httpx.TimeoutException as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        await _log_to_master(request_id, model, task, 0, 0, latency_ms, "timeout", 0.0)
        raise ClaudeError("Claude API timeout") from exc


async def draft_json(
    system_prompt: str,
    user_prompt: str,
    task: str = "demand",
    demand_amount: float | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
) -> dict[str, Any]:
    text = await draft(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        task=task,
        demand_amount=demand_amount,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    clean = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError as exc:
        logger.error("Claude JSON parse failed: %s\nRaw: %s", exc, clean[:500])
        raise ClaudeError(f"Claude response was not valid JSON: {exc}") from exc
