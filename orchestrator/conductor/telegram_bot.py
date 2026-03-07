"""
Telegram bot interface for master-conductor.
Dual-principal routing: Eric (technical) + David (sales).
Critical alerts broadcast to both. Role-specific alerts route to domain owner only.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logger = logging.getLogger("conductor.telegram")

ALERT_INFO     = "ℹ️"
ALERT_WARN     = "⚠️"
ALERT_CRITICAL = "🔴"

# Alert level → which roles receive it
# critical/info always go to both
# infrastructure alerts → technical only (Eric)
# revenue/approval alerts → sales only (David)
ROLE_ROUTING: dict[str, list[str]] = {
    "critical":       ["technical", "sales"],   # both always
    "info":           ["technical", "sales"],   # both always
    "warn":           ["technical", "sales"],   # both always
    "infrastructure": ["technical"],             # Eric only
    "gpu":            ["technical"],             # Eric only
    "spend":          ["technical", "sales"],   # both (money affects both)
    "approval":       ["sales"],                 # David only
    "revenue":        ["sales"],                 # David only
    "client":         ["sales"],                 # David only
    "outreach":       ["sales"],                 # David only
}


class ConductorBot:
    def __init__(self, token: str) -> None:
        self.token = token
        self._chat_ids: set[int] = set()
        # chat_id → role mapping loaded from postgres
        self._roles: dict[int, str] = {}
        self._app: Application | None = None

    async def load_chat_ids(self) -> None:
        """Load persisted chat_ids and roles from postgres on startup."""
        from db import get_chat_ids_with_roles
        principals = await get_chat_ids_with_roles()
        self._chat_ids = {p["chat_id"] for p in principals}
        self._roles = {p["chat_id"]: p["role"] for p in principals}
        logger.info(
            "Loaded %d principal(s): %s",
            len(principals),
            [(p["username"], p["role"]) for p in principals],
        )

    async def build(self) -> Application:
        self._app = Application.builder().token(self.token).build()
        self._app.add_handler(CommandHandler("start",   self._cmd_start))
        self._app.add_handler(CommandHandler("status",  self._cmd_status))
        self._app.add_handler(CommandHandler("health",  self._cmd_health))
        self._app.add_handler(CommandHandler("help",    self._cmd_help))
        self._app.add_handler(CommandHandler("whoami",  self._cmd_whoami))
        return self._app

    # ------------------------------------------------------------------ #
    # Inbound commands                                                     #
    # ------------------------------------------------------------------ #

    async def _cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        from db import save_chat_id
        chat_id = update.effective_chat.id
        username = update.effective_user.username
        self._chat_ids.add(chat_id)
        # Assign role based on known principals
        role = self._roles.get(chat_id, "principal")
        await save_chat_id(chat_id, username)
        logger.info("Registered chat_id=%s username=%s role=%s", chat_id, username, role)
        await update.message.reply_text(
            f"{ALERT_INFO} *Miko Conductor online.*\n"
            f"Chat ID: `{chat_id}`\n"
            f"Role: `{role}`\n\n"
            "Commands: /status /health /whoami /help",
            parse_mode="Markdown",
        )

    async def _cmd_whoami(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        role = self._roles.get(chat_id, "unknown")
        username = update.effective_user.username
        domain_map = {
            "technical": "Infrastructure · Build · GPU · Spend",
            "sales":     "Clients · Outreach · Revenue · Approvals",
        }
        domain = domain_map.get(role, "General")
        await update.message.reply_text(
            f"{ALERT_INFO} *Principal context*\n\n"
            f"Username: `@{username}`\n"
            f"Chat ID: `{chat_id}`\n"
            f"Role: `{role}`\n"
            f"Domain: {domain}",
            parse_mode="Markdown",
        )

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        chat_id = update.effective_chat.id
        role = self._roles.get(chat_id, "principal")
        await update.message.reply_text(
            f"{ALERT_INFO} *Conductor status:* running\n"
            f"Principals registered: {len(self._chat_ids)}\n"
            f"Your role: `{role}`\n"
            "Use /health to poll all services now.",
            parse_mode="Markdown",
        )

    async def _cmd_health(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        from health import poll_all, down_services
        chat_id = update.effective_chat.id
        role = self._roles.get(chat_id, "principal")
        await update.message.reply_text(f"{ALERT_INFO} Polling all services...")
        results = await poll_all()
        down = down_services(results)
        up_count = len(results) - len(down)

        lines = [f"*Health check — {len(results)} services*\n"]
        lines.append(f"✅ Up: {up_count}   🔴 Down: {len(down)}\n")

        # Technical role sees all services with latency
        # Sales role sees summary + any down services only
        if role == "technical":
            for r in results:
                icon = "✅" if r.status == "up" else "🔴"
                lines.append(f"{icon} `{r.service}` ({r.latency_ms:.0f}ms)")
        else:
            if down:
                lines.append("\n*Degraded services:*")
                for r in down:
                    lines.append(f"🔴 `{r.service}`")
            else:
                lines.append("All systems operational.")

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "*Miko Conductor commands*\n\n"
            "/status — conductor status and your role\n"
            "/health — poll all services now\n"
            "/whoami — your principal context\n"
            "/help — this message",
            parse_mode="Markdown",
        )

    # ------------------------------------------------------------------ #
    # Outbound alerts — role-aware routing                                #
    # ------------------------------------------------------------------ #

    async def alert(self, level: str, message: str) -> None:
        """
        Send alert to principals whose role matches the alert level routing.
        level: critical | info | warn | infrastructure | gpu | spend |
               approval | revenue | client | outreach
        """
        if not self._chat_ids:
            logger.warning("No principals registered — alert suppressed: %s", message)
            return

        target_roles = ROLE_ROUTING.get(level, ["technical", "sales"])
        icon = {
            "info":           ALERT_INFO,
            "warn":           ALERT_WARN,
            "critical":       ALERT_CRITICAL,
            "infrastructure": "🖥️",
            "gpu":            "⚡",
            "spend":          "💰",
            "approval":       "✋",
            "revenue":        "📈",
            "client":         "👤",
            "outreach":       "📧",
        }.get(level, ALERT_INFO)

        for chat_id in self._chat_ids:
            role = self._roles.get(chat_id, "principal")
            if role not in target_roles:
                logger.debug(
                    "Suppressed %s alert for chat_id=%s role=%s",
                    level, chat_id, role,
                )
                continue
            try:
                await self._app.bot.send_message(
                    chat_id=chat_id,
                    text=f"{icon} {message}",
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.error("Failed to send alert to chat_id=%s: %s", chat_id, exc)

    async def alert_to(self, chat_id: int, level: str, message: str) -> None:
        """Send alert directly to a specific principal by chat_id."""
        icon = {
            "info": ALERT_INFO, "warn": ALERT_WARN, "critical": ALERT_CRITICAL,
        }.get(level, ALERT_INFO)
        try:
            await self._app.bot.send_message(
                chat_id=chat_id,
                text=f"{icon} {message}",
                parse_mode="Markdown",
            )
        except Exception as exc:
            logger.error("Failed to send direct alert to chat_id=%s: %s", chat_id, exc)
