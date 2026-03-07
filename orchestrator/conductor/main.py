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

from db import close_pool
from health import down_services, poll_all
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


async def daily_brief_job() -> None:
    results = await poll_all()
    down = down_services(results)
    up_count = len(results) - len(down)

    lines = [
        f"*☀️ Daily brief — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}*\n",
        f"Services: ✅ {up_count} up   🔴 {len(down)} down",
    ]
    if down:
        lines.append("\nDown:")
        for r in down:
            lines.append(f"  • `{r.service}`")

    await bot.alert("info", "\n".join(lines))



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
        hour=8,
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

    logger.info("master-conductor ready — starting Telegram polling")
    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        await stop
        await app.updater.stop()
        await app.stop()

    await close_pool()
    logger.info("master-conductor stopped cleanly")


if __name__ == "__main__":
    asyncio.run(main())
