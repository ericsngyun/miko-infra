"""
Spend governor — polls each project's /spend endpoint every 15 minutes.
Warns at 80% of daily cap. Hard-stop alert at 100%.
"""
from __future__ import annotations

import logging

import httpx

from settings import settings

logger = logging.getLogger("conductor.spend")

# Project spend endpoints and their daily caps
PROJECTS = [
    {
        "name": "pleadly",
        "url": "http://pleadly-api:8300/spend",
        "cap_usd": settings.spend_cap_pleadly,
    },
]


async def check_all_spend() -> list[dict]:
    """Poll all project spend endpoints. Returns list of spend results."""
    results = []
    async with httpx.AsyncClient(timeout=5.0) as client:
        for project in PROJECTS:
            try:
                resp = await client.get(project["url"])
                if resp.status_code == 200:
                    data = resp.json()
                    usd = float(data.get("usd", 0.0))
                else:
                    logger.warning("Spend check failed for %s: HTTP %s", project["name"], resp.status_code)
                    continue
            except Exception as exc:
                logger.warning("Spend check error for %s: %s", project["name"], exc)
                continue

            cap = project["cap_usd"]
            pct = (usd / cap * 100) if cap > 0 else 0

            results.append({
                "name": project["name"],
                "usd": usd,
                "cap": cap,
                "pct": pct,
            })

            if pct >= 100:
                logger.error("SPEND CAP EXCEEDED: %s $%.2f / $%.2f", project["name"], usd, cap)
            elif pct >= 80:
                logger.warning("SPEND WARNING: %s $%.2f / $%.2f (%.0f%%)", project["name"], usd, cap, pct)
            else:
                logger.info("Spend OK: %s $%.2f / $%.2f (%.0f%%)", project["name"], usd, cap, pct)

    return results
