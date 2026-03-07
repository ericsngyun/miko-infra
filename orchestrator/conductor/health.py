"""
Health poller — checks all stack endpoints every HEALTH_POLL_INTERVAL_S seconds.
HTTP services checked via GET. TCP services (postgres, redis) checked via socket.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger("conductor.health")

HTTP_SERVICES: list[dict] = [
    {"project": "pleadly", "name": "pleadly-api",  "url": "http://pleadly-api:8300/health"},
    {"project": "monitor", "name": "grafana",       "url": "http://grafana:3000/api/health"},
    {"project": "monitor", "name": "prometheus",    "url": "http://prometheus:9090/-/healthy"},
    {"project": "shared",  "name": "caddy",         "url": "http://caddy:80"},
    {"project": "awaas",   "name": "awaas-n8n",     "url": "http://awaas-n8n:5679/healthz"},
]

TCP_SERVICES: list[dict] = [
    {"project": "pleadly", "name": "pleadly-postgres", "host": "pleadly-postgres", "port": 5432},
    {"project": "shared",  "name": "redis",             "host": "redis",            "port": 6379},
    {"project": "awaas",   "name": "awaas-postgres",    "host": "awaas-postgres",   "port": 5432},
    {"project": "trading", "name": "trading-postgres",  "host": "trading-postgres", "port": 5432},
]


@dataclass
class HealthResult:
    project: str
    service: str
    status: str           # "up" | "down"
    status_code: int | None
    latency_ms: float
    checked_at: datetime


async def _check_tcp(host: str, port: int, timeout: float = 3.0) -> bool:
    """Check if a TCP port is accepting connections."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def poll_all() -> list[HealthResult]:
    results: list[HealthResult] = []

    # HTTP checks
    async with httpx.AsyncClient(timeout=5.0) as client:
        for svc in HTTP_SERVICES:
            t0 = datetime.now(timezone.utc)
            try:
                resp = await client.get(svc["url"])
                latency = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
                status = "up" if resp.status_code < 500 else "down"
                code = resp.status_code
            except Exception as exc:
                latency = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
                status = "down"
                code = None
                logger.warning("HTTP check failed for %s: %s", svc["name"], exc)

            results.append(HealthResult(
                project=svc["project"],
                service=svc["name"],
                status=status,
                status_code=code,
                latency_ms=latency,
                checked_at=t0,
            ))

    # TCP checks
    for svc in TCP_SERVICES:
        t0 = datetime.now(timezone.utc)
        up = await _check_tcp(svc["host"], svc["port"])
        latency = (datetime.now(timezone.utc) - t0).total_seconds() * 1000
        if not up:
            logger.warning("TCP check failed for %s:%s", svc["host"], svc["port"])
        results.append(HealthResult(
            project=svc["project"],
            service=svc["name"],
            status="up" if up else "down",
            status_code=None,
            latency_ms=latency,
            checked_at=t0,
        ))

    return results


def down_services(results: list[HealthResult]) -> list[HealthResult]:
    return [r for r in results if r.status == "down"]
