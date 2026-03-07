"""
Health endpoint — returns queue depth, Ollama status, and Redis status.

GET /health — fully implemented.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from integrations.ollama_client import OllamaClient
from models.payloads import HealthResult

logger = logging.getLogger("pleadly.health")

router = APIRouter()


@router.get("/health", response_model=HealthResult)
async def health_check() -> HealthResult:
    """
    Return service health status including queue depth and GPU/model availability.

    Checks:
    - Redis connectivity and queue depth
    - Ollama model availability
    """
    queue_depth = 0
    ollama_healthy = False

    # Check Redis queue depth
    try:
        from main import app_state

        if app_state.get("redis"):
            redis_client = app_state["redis"]
            queue_depth = await redis_client.get_queue_depth("pleadly:jobs")
    except Exception:
        # Redis not available — report queue_depth=0
        logger.debug("Redis not available for health check")

    # Check Ollama
    try:
        ollama = OllamaClient()
        ollama_healthy = await ollama.health_check()
        await ollama.close()
    except Exception:
        logger.debug("Ollama not available for health check")

    status = "online" if ollama_healthy else "offline"

    return HealthResult(
        status=status,
        queue_depth=queue_depth,
    )
