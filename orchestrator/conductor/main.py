"""
master-conductor — Miko Labs AWaaS nervous system.
Polls stack health, manages Telegram alerts, governs spend.
Chat_ids persisted to postgres — survives restarts.
"""
from __future__ import annotations

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db import close_pool, log_health, upsert_infrastructure_state
from health import down_services, poll_all, HTTP_SERVICES_EXTENDED
from spend_governor import check_all_spend
from settings import settings
from telegram_bot import ConductorBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("conductor.main")

_previously_down: set[str] = set()
bot = ConductorBot(token=settings.telegram_bot_token)


async def health_poll_job() -> None:
    global _previously_down
    results = await poll_all()
    down = {r.service for r in down_services(results)}

    # Persist all results to health_log and infrastructure_state
    for r in results:
        await log_health(r.service, r.project, r.status)
        await upsert_infrastructure_state(
            service=r.service,
            project_name=r.project if r.project not in ("monitor", "shared") else None,
            status=r.status,
            response_ms=int(r.latency_ms),
            detail={"status_code": r.status_code, "latency_ms": round(r.latency_ms, 1)},
        )

    # Also poll extended services (llama-server, miko)
    import httpx as _httpx
    from datetime import datetime as _dt, timezone as _tz
    async with _httpx.AsyncClient(timeout=5.0) as _client:
        for _svc in HTTP_SERVICES_EXTENDED:
            _t0 = _dt.now(_tz.utc)
            try:
                _resp = await _client.get(_svc["url"])
                _latency = (_dt.now(_tz.utc) - _t0).total_seconds() * 1000
                _status = "up" if _resp.status_code < 500 else "down"
                _code = _resp.status_code
            except Exception as _exc:
                _latency = (_dt.now(_tz.utc) - _t0).total_seconds() * 1000
                _status = "down"
                _code = None
                logger.warning("Extended health check failed for %s: %s", _svc["name"], _exc)
            await upsert_infrastructure_state(
                service=_svc["name"],
                project_name=_svc["project"] if _svc["project"] not in ("monitor", "shared") else None,
                status=_status,
                response_ms=int(_latency),
                detail={"status_code": _code, "latency_ms": round(_latency, 1)},
            )

    for svc in down - _previously_down:
        logger.error("SERVICE DOWN: %s", svc)
        await bot.alert(
            "critical",
            f"*SERVICE DOWN:* `{svc}`\n"
            f"{datetime.now(timezone.utc).strftime('%H:%M UTC')}",
        )

    for svc in _previously_down - down:
        logger.info("SERVICE RECOVERED: %s", svc)
        await bot.alert(
            "info",
            f"*SERVICE RECOVERED:* `{svc}`\n"
            f"{datetime.now(timezone.utc).strftime('%H:%M UTC')}",
        )

    _previously_down = down

    if not down:
        logger.info("Health poll complete — all %d services up", len(results))
    else:
        logger.warning("Health poll complete — %d down: %s", len(down), down)


async def get_open_tasks(owner: str) -> list[dict]:
    """Fetch open tasks for a principal from master-postgres."""
    try:
        import asyncpg as _asyncpg
        conn = await _asyncpg.connect(host=settings.pg_host, port=settings.pg_port, user=settings.pg_user, password=settings.pg_password, database=settings.pg_database)
        rows = await conn.fetch(
            """SELECT id, title, priority, status, due_date
               FROM tasks
               WHERE (owner = $1 OR owner = 'both')
               AND status != 'done'
               ORDER BY priority, due_date NULLS LAST
               LIMIT 10""",
            owner
        )
        await conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("get_open_tasks failed: %s", e)
        return []


async def daily_brief_job() -> None:
    """Send 9am morning brief to Eric and David with infra state + tasks."""
    results = await poll_all()
    down = down_services(results)
    up_count = len(results) - len(down)
    today = datetime.now(timezone.utc).strftime("%A, %B %d, %Y")

    infra_line = f"✅ {up_count}/{len(results)} services OK"
    if down:
        names = ", ".join(f"`{r.service}`" for r in down)
        infra_line = f"🔴 {len(down)} degraded: {names}"

    # Get spend
    spend_line = ""
    try:
        import asyncpg as _asyncpg
        conn = await _asyncpg.connect(host=settings.pg_host, port=settings.pg_port, user=settings.pg_user, password=settings.pg_password, database=settings.pg_database)
        rows = await conn.fetch(
            "SELECT key, value FROM conductor_state WHERE key IN ('total_spend_usd', 'budget_usd')"
        )
        await conn.close()
        kv = {r["key"]: float(r["value"]) for r in rows}
        spend = kv.get("total_spend_usd", 0.0)
        budget = kv.get("budget_usd", 90.0)
        pct = (spend / budget * 100) if budget else 0
        spend_line = f"💰 Spend: ${spend:.2f} / ${budget:.0f} ({pct:.0f}%)"
    except Exception as e:
        logger.warning("Brief spend fetch failed: %s", e)
        spend_line = "💰 Spend: unavailable"

    principals = [
        {"user": "eric",  "chat_id": 7355900090,  "name": "Eric"},
        {"user": "david", "chat_id": 1697120532,  "name": "David"},
    ]

    for p in principals:
        tasks = await get_open_tasks(p["user"])

        if tasks:
            task_lines = []
            for t in tasks:
                icon = "🔴" if t["priority"] == 1 else "🟡" if t["priority"] == 2 else "🔵"
                due = f" — due {t['due_date']}" if t["due_date"] else ""
                task_lines.append(f"{icon} [{t['id']}] {t['title']}{due}")
            tasks_block = "\n".join(task_lines)
        else:
            tasks_block = "✅ No open tasks"

        brief = (
            f"*☀️ Good morning, {p['name']}* — {today}\n\n"
            f"*Infrastructure*\n{infra_line}\n{spend_line}\n\n"
            f"*Open Tasks*\n{tasks_block}\n\n"
            f"_Talk to Miko to add tasks, mark done, or get today's strategy focus._"
        )

        try:
            import httpx as _httpx
            async with _httpx.AsyncClient(timeout=10.0) as _client:
                await _client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json={"chat_id": p["chat_id"], "text": brief, "parse_mode": "Markdown"}
                )
            logger.info("Morning brief sent to %s", p["name"])
        except Exception as _e:
            logger.warning("Brief send failed for %s: %s", p["name"], _e)


async def spend_poll_job() -> None:
    """Check all project spend and alert via Telegram if thresholds breached."""
    results = await check_all_spend()
    for r in results:
        if r["pct"] >= 100:
            await bot.alert(
                "critical",
                f"*SPEND CAP EXCEEDED*\n"
                f"Project: `{r['name']}`\n"
                f"Spent: ${r['usd']:.2f} / ${r['cap']:.2f} ({r['pct']:.0f}%)\n"
                f"Action: outbound calls halted",
            )
        elif r["pct"] >= 80:
            await bot.alert(
                "warning",
                f"*Spend warning*\n"
                f"Project: `{r['name']}`\n"
                f"Spent: ${r['usd']:.2f} / ${r['cap']:.2f} ({r['pct']:.0f}%)",
            )


async def main() -> None:
    logger.info("Starting master-conductor")

    # Load persisted chat_ids from postgres before anything else
    await bot.load_chat_ids()

    # Build Telegram bot
    app = await bot.build()

    # Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        health_poll_job,
        "interval",
        seconds=settings.health_poll_interval_s,
        id="health_poll",
    )
    scheduler.add_job(
        daily_brief_job,
        "cron",
        hour=17,
        minute=0,
        id="daily_brief",
    )
    scheduler.add_job(
        spend_poll_job,
        "interval",
        minutes=15,
        id="spend_poll",
    )
    scheduler.start()
    logger.info(
        "Scheduler started — health poll every %ds, daily brief at 08:00 UTC",
        settings.health_poll_interval_s,
    )

    # First health poll on startup
    await health_poll_job()

    # Graceful shutdown
    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("Received %s — shutting down", sig.name)
        scheduler.shutdown(wait=False)
        if not stop.done():
            stop.set_result(None)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown, sig)

    logger.info("master-conductor ready — alert-only mode (no polling)")
    async with app:
        await app.initialize()
        await app.bot.get_me()  # verify token is valid
        await stop
        await app.shutdown()

    await close_pool()
    logger.info("master-conductor stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
