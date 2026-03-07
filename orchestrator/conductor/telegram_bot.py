"""
Telegram bot interface for master-conductor.
Supports multiple chat_ids (Eric + David). Persists to postgres.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger("conductor.telegram")

ALERT_INFO     = "ℹ️"
ALERT_WARN     = "⚠️"
ALERT_CRITICAL = "🔴"


class ConductorBot:
    def __init__(self, token: str) -> None:
        self.token = token
        self._chat_ids: set[int] = set()
        self._app: Application | None = None

    async def load_chat_ids(self) -> None:
        """Load persisted chat_ids from postgres on startup."""
        from db import get_chat_ids
        ids = await get_chat_ids()
        self._chat_ids = set(ids)
        logger.info("Loaded %d chat_id(s) from database: %s", len(ids), ids)

    async def build(self) -> Application:
        self._app = Application.builder().token(self.token).build()
        self._app.add_handler(CommandHandler("start",  self._cmd_start))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("health", self._cmd_health))
        self._app.add_handler(CommandHandler("help",   self._cmd_help))
        return self._app

    # ------------------------------------------------------------------ #
    # Inbound commands                                                     #
    # ------------------------------------------------------------------ #

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        from db import save_chat_id
        chat_id = update.effective_chat.id
        username = update.effective_user.username
        self._chat_ids.add(chat_id)
        await save_chat_id(chat_id, username)
        logger.info("Registered chat_id=%s username=%s", chat_id, username)
        await update.message.reply_text(
            f"{ALERT_INFO} *Miko Conductor online.*\n"
            f"Chat ID registered: `{chat_id}`\n\n"
            "Commands: /status /health /help",
            parse_mode="Markdown",
        )

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            f"{ALERT_INFO} *Conductor status:* running\n"
            f"Registered users: {len(self._chat_ids)}\n"
            "Use /health to poll all services now.",
            parse_mode="Markdown",
        )

    async def _cmd_health(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        from health import poll_all, down_services
        await update.message.reply_text(f"{ALERT_INFO} Polling all services...")
        results = await poll_all()
        down = down_services(results)
        up_count = len(results) - len(down)

        lines = [f"*Health check — {len(results)} services*\n"]
        lines.append(f"✅ Up: {up_count}   🔴 Down: {len(down)}\n")
        for r in results:
            icon = "✅" if r.status == "up" else "🔴"
            lines.append(f"{icon} `{r.service}` ({r.latency_ms:.0f}ms)")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "*Miko Conductor commands*\n\n"
            "/status — conductor health\n"
            "/health — poll all services now\n"
            "/help — this message",
            parse_mode="Markdown",
        )

    # ------------------------------------------------------------------ #
    # Outbound alerts — broadcasts to ALL registered chat_ids             #
    # ------------------------------------------------------------------ #

    async def alert(self, level: str, message: str) -> None:
        if not self._chat_ids:
            logger.warning("No chat_ids registered — alert suppressed: %s", message)
            return
        icon = {
            "info":     ALERT_INFO,
            "warn":     ALERT_WARN,
            "critical": ALERT_CRITICAL,
        }.get(level, ALERT_INFO)
        for chat_id in self._chat_ids:
            try:
                await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=f"{icon} {message}",
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.error("Failed to send alert to chat_id=%s: %s", chat_id, exc)
