"""
Claude API client for high-quality legal document drafting.

Routing:
  - Demand letters, discovery requests/responses → claude-sonnet-4-6
  - High-stakes demands (>$500K) → claude-opus-4-6
  - Quick classification, structured extraction → local Qwen (ollama_client)

Privacy model:
  - Raw PRIVILEGED documents never leave local inference
  - Only synthesized summaries (caseSummary, medicalSummary, etc.) go to Claude
  - This preserves ABA Opinion 512 compliance argument
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("pleadly.claude")

ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL   = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION   = "2023-06-01"

# Model routing
MODEL_DRAFTING      = os.getenv("CLAUDE_DRAFTING_MODEL",   "claude-sonnet-4-6")
MODEL_HEAVY         = os.getenv("CLAUDE_HEAVY_MODEL",      "claude-opus-4-6")
MODEL_FAST          = os.getenv("CLAUDE_FAST_MODEL",       "claude-haiku-4-5-20251001")

# Demand threshold for opus escalation (special damages * multiplier)
OPUS_THRESHOLD      = float(os.getenv("CLAUDE_OPUS_THRESHOLD", "500000"))


class ClaudeError(Exception):
    """Raised when Claude API returns an error."""
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _select_model(task: str, demand_amount: float | None = None) -> str:
    """
    Route to appropriate Claude model based on task type and stakes.

    Args:
        task: One of 'demand', 'discovery', 'draft', 'fast'
        demand_amount: Estimated demand value for opus escalation

    Returns:
        Model string to use
    """
    if task == "fast":
        return MODEL_FAST
    if demand_amount and demand_amount >= OPUS_THRESHOLD:
        logger.info("Escalating to Opus — demand amount $%.0f >= threshold $%.0f",
                    demand_amount, OPUS_THRESHOLD)
        return MODEL_HEAVY
    return MODEL_DRAFTING


async def draft(
    system_prompt: str,
    user_prompt: str,
    task: str = "demand",
    demand_amount: float | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.3,
) -> str:
    """
    Send a drafting request to Claude and return the text response.

    Args:
        system_prompt: Attorney persona + jurisdiction + formatting instructions
        user_prompt: Case facts, summaries, specific instructions
        task: Routing hint — 'demand', 'discovery', 'draft', 'fast'
        demand_amount: Optional demand value for opus escalation
        max_tokens: Response length limit
        temperature: Lower = more consistent legal prose

    Returns:
        Raw text response from Claude

    Raises:
        ClaudeError: On API error or missing key
    """
    if not ANTHROPIC_API_KEY:
        raise ClaudeError("ANTHROPIC_API_KEY not configured in pleadly-api")

    model = _select_model(task, demand_amount)
    logger.info("Claude draft request task=%s model=%s max_tokens=%d", task, model, max_tokens)

    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=10.0, pool=5.0)) as client:
        try:
            resp = await client.post(
                ANTHROPIC_API_URL,
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise ClaudeError(
                f"Claude API error {exc.response.status_code}: {exc.response.text[:300]}",
                status_code=exc.response.status_code,
            ) from exc
        except httpx.TimeoutException as exc:
            raise ClaudeError("Claude API timeout") from exc

    data = resp.json()
    usage = data.get("usage", {})
    logger.info(
        "Claude draft complete model=%s in=%d out=%d",
        model,
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
    )
    return data["content"][0]["text"]


async def draft_json(
    system_prompt: str,
    user_prompt: str,
    task: str = "demand",
    demand_amount: float | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
) -> dict[str, Any]:
    """
    Same as draft() but parses JSON response.
    Strips markdown fences before parsing.
    """
    import json, re
    text = await draft(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        task=task,
        demand_amount=demand_amount,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    # Strip ```json fences
    clean = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError as exc:
        logger.error("Claude JSON parse failed: %s\nRaw: %s", exc, clean[:500])
        raise ClaudeError(f"Claude response was not valid JSON: {exc}") from exc
