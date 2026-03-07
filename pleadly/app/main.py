"""
Pleadly Intelligence Plane — FastAPI application.

This is the main entry point for the FastAPI service that handles all AI
inference workloads. It receives HMAC-signed requests from the Next.js
Control Plane and processes them locally using Ollama models.

CRITICAL: Never log case content or PII. Only log request IDs and metadata.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic_settings import BaseSettings
from pydantic import ConfigDict

# ---------------------------------------------------------------------------
# Configuration via pydantic-settings
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8")

    # HMAC authentication
    pleadly_hmac_secret: str = ""

    # CORS — Tailscale IPs and local dev
    cors_allowed_origins: list[str] = [
        "http://localhost:3000",
        "http://100.64.0.0/10",  # Tailscale CGNAT range
    ]

    # Ollama
    ollama_url: str = "http://localhost:11434"

    # Redis
    redis_url: str = "redis://localhost:6379"
    redis_db: int = 0

    # PostgreSQL
    postgres_dsn: str = ""

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None

    # Service
    log_level: str = "INFO"
    max_request_age_seconds: int = 300  # 5 minute HMAC timestamp tolerance


settings = Settings()

# ---------------------------------------------------------------------------
# Logging — NEVER log case content or PII
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("pleadly.main")

# ---------------------------------------------------------------------------
# Global application state (connection pools, clients)
# ---------------------------------------------------------------------------

app_state: dict[str, Any] = {}

# ---------------------------------------------------------------------------
# Lifecycle management
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Application startup and shutdown lifecycle.

    Initializes connection pools for Redis, PostgreSQL, Qdrant,
    and verifies Ollama connectivity.
    """
    logger.info("Starting Pleadly Intelligence Plane")

    # Initialize Ollama client
    from integrations.ollama_client import OllamaClient

    ollama = OllamaClient(base_url=settings.ollama_url)
    ollama_healthy = await ollama.health_check()
    if ollama_healthy:
        logger.info("Ollama connection verified at %s", settings.ollama_url)
    else:
        logger.warning(
            "Ollama not reachable at %s — AI endpoints will fail",
            settings.ollama_url,
        )
    app_state["ollama"] = ollama

    # Initialize Redis (best-effort — service works without it)
    try:
        from integrations.redis_client import PleadlyRedisClient

        redis_client = PleadlyRedisClient(url=settings.redis_url, db=settings.redis_db)
        # Redis client is stubbed — connection will fail gracefully
        app_state["redis"] = redis_client
        logger.info("Redis client initialized (stubbed)")
    except Exception as exc:
        logger.warning("Redis initialization skipped: %s", exc)
        app_state["redis"] = None

    # Initialize PostgreSQL (best-effort)
    if settings.postgres_dsn:
        try:
            from integrations.postgres_client import PostgresClient

            pg = PostgresClient(dsn=settings.postgres_dsn)
            app_state["postgres"] = pg
            logger.info("PostgreSQL client initialized (stubbed)")
        except Exception as exc:
            logger.warning("PostgreSQL initialization skipped: %s", exc)
            app_state["postgres"] = None
    else:
        app_state["postgres"] = None
        logger.info("PostgreSQL DSN not configured — skipping")

    # Initialize Qdrant (best-effort)
    try:
        from integrations.qdrant_client import PleadlyQdrantClient

        qdrant = PleadlyQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key,
        )
        app_state["qdrant"] = qdrant
        logger.info("Qdrant client initialized (stubbed)")
    except Exception as exc:
        logger.warning("Qdrant initialization skipped: %s", exc)
        app_state["qdrant"] = None

    logger.info("Pleadly Intelligence Plane started")

    yield  # Application runs

    # Shutdown
    logger.info("Shutting down Pleadly Intelligence Plane")

    if app_state.get("ollama"):
        await app_state["ollama"].close()

    if app_state.get("redis"):
        try:
            await app_state["redis"].close()
        except Exception:
            pass

    if app_state.get("postgres"):
        try:
            await app_state["postgres"].close()
        except Exception:
            pass

    if app_state.get("qdrant"):
        try:
            await app_state["qdrant"].close()
        except Exception:
            pass

    logger.info("Pleadly Intelligence Plane stopped")


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Pleadly Intelligence Plane",
    description="Local AI inference service for the Pleadly legal platform",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS middleware — allow Tailscale IPs and local dev
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# HMAC validation middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def hmac_validation_middleware(request: Request, call_next: Any) -> Response:
    """
    Validate HMAC signature on all requests except GET /health.

    The signature algorithm matches intelligence.ts:
        message = "{timestamp}.{body}"
        signature = HMAC-SHA256(secret, message)

    Headers required:
        X-Pleadly-Signature: hex-encoded HMAC
        X-Pleadly-Timestamp: Unix timestamp string
    """
    # Skip HMAC for health checks and docs
    if request.url.path in ("/health", "/spend", "/docs", "/openapi.json", "/redoc"):
        return await call_next(request)

    # Skip if no HMAC secret configured (development mode)
    if not settings.pleadly_hmac_secret:
        logger.debug("HMAC validation skipped — no secret configured")
        return await call_next(request)

    # Extract headers
    signature = request.headers.get("X-Pleadly-Signature")
    timestamp_str = request.headers.get("X-Pleadly-Timestamp")
    request_id = request.headers.get("X-Request-Id", "unknown")

    if not signature or not timestamp_str:
        logger.warning("Missing HMAC headers request_id=%s", request_id)
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing authentication headers"},
        )

    # Validate timestamp freshness
    try:
        timestamp = int(timestamp_str)
        now = int(time.time())
        if abs(now - timestamp) > settings.max_request_age_seconds:
            logger.warning(
                "Stale HMAC timestamp request_id=%s age=%ds",
                request_id,
                abs(now - timestamp),
            )
            return JSONResponse(
                status_code=401,
                content={"detail": "Request timestamp too old"},
            )
    except ValueError:
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid timestamp"},
        )

    # Read body for signature validation
    body = await request.body()

    # Compute expected signature: HMAC-SHA256("{timestamp}.{body}")
    message = f"{timestamp_str}.{body.decode('utf-8')}"
    expected = hmac.new(
        settings.pleadly_hmac_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison
    if not hmac.compare_digest(signature, expected):
        logger.warning("HMAC signature mismatch request_id=%s", request_id)
        return JSONResponse(
            status_code=401,
            content={"detail": "Invalid signature"},
        )

    # Log request metadata only — NEVER log body content
    logger.info(
        "Authenticated request path=%s request_id=%s",
        request.url.path,
        request_id,
    )

    return await call_next(request)


# ---------------------------------------------------------------------------
# Register routers
# ---------------------------------------------------------------------------

from routers import (  # noqa: E402
    analyze,
    bills,
    chronology,
    classify,
    cross_analyze,
    demand,
    demand_edit,
    demand_rating,
    discovery,
    health,
    intake,
    liens,
    queue,
    sol,
    spend,
    store,
)

app.include_router(health.router)
app.include_router(analyze.router)
app.include_router(classify.router)
app.include_router(cross_analyze.router)
app.include_router(demand.router)
app.include_router(demand_edit.router)
app.include_router(demand_rating.router)
app.include_router(discovery.router)
app.include_router(bills.router)
app.include_router(liens.router)
app.include_router(sol.router)
app.include_router(chronology.router)
app.include_router(intake.router)
app.include_router(store.router)
app.include_router(queue.router)
app.include_router(spend.router)
