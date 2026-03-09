"""
Miko — Personal Intelligence Assistant
Telegram bot + Web UI backend
MVP · March 2026

Stack:
- python-telegram-bot for Telegram
- FastAPI for web UI backend
- Mem0 (Qdrant backend) for persistent memory
- httpx for Pleadly health checks
- Qwen3.5-35B-A3B via llama-server for reasoning
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import json
import subprocess
import re
import shlex
import uuid
from collections import deque
try:
    import asyncpg as _asyncpg
    ASYNCPG_AVAILABLE = True
except ImportError:
    _asyncpg = None
    ASYNCPG_AVAILABLE = False
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LLAMA_SERVER_URL = os.getenv("LLAMA_SERVER_URL", "http://172.23.0.1:11435")
EMBED_SERVER_URL = os.getenv("EMBED_SERVER_URL", "http://172.23.0.1:11436")
EMBED_MODEL      = os.getenv("EMBED_MODEL",        "nomic-embed-text")
PLEADLY_API_URL  = os.getenv("PLEADLY_API_URL",  "http://172.23.0.1:8300")
QDRANT_URL       = os.getenv("QDRANT_URL",        "http://qdrant:6333")
QDRANT_API_KEY   = os.getenv("QDRANT_API_KEY",    None)
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
ERIC_CHAT_ID     = int(os.getenv("ERIC_CHAT_ID",   "7355900090"))
DAVID_CHAT_ID    = int(os.getenv("DAVID_CHAT_ID",  "1697120532"))
MODEL            = os.getenv("MIKO_MODEL",         "qwen3.5")
FAST_MODEL       = os.getenv("MIKO_FAST_MODEL",    "Qwen3.5-4B-Q4_K_M.gguf")
FAST_SERVER_URL  = os.getenv("FAST_SERVER_URL",    "http://172.23.0.1:11437")

SOUL_MD_PATH = Path(__file__).parent / "SOUL.md"

# ---------------------------------------------------------------------------
# Session history — rolling 20-message buffer per principal
# ---------------------------------------------------------------------------
_session_history: dict[str, deque] = {}

# ---------------------------------------------------------------------------
# Postgres DSN — pulled from env set in docker-compose
# ---------------------------------------------------------------------------
MASTER_POSTGRES_DSN = os.getenv(
    "MASTER_POSTGRES_DSN",
    "postgresql://awaas_master:@master-postgres:5432/awaas_master"
)

# ---------------------------------------------------------------------------
# Permission system
# ---------------------------------------------------------------------------
# Tier: "green" = auto, "yellow" = ask + 2min auto-approve, "red" = always ask
TOOL_PERMISSION_TIER: dict[str, str] = {
    "task_list":   "green",
    "infra_query": "green",
    "remember":    "green",
    "task_add":    "yellow",
    "task_update": "yellow",
    "shell_exec":  "yellow",   # individual calls may be escalated to red inside the executor
}

# Pending approvals: uuid → {tool_name, tool_args, user_id, event, result, approved}
_pending_approvals: dict[str, dict] = {}

# request_permission removed — replaced by non-blocking execute_tool


async def handle_approval_callback(update, context) -> None:
    """Handle ✅/❌ button presses — execute tool immediately on approval."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    if ":" not in data:
        return

    action, approval_id = data.split(":", 1)
    if approval_id not in _pending_approvals:
        await query.edit_message_text("⚠️ This request has already been handled or expired.")
        return

    entry = _pending_approvals.pop(approval_id)
    tool_name = entry["tool_name"]
    tool_args = entry["tool_args"]
    user_id = entry["user_id"]
    chat_id = entry.get("chat_id", ERIC_CHAT_ID)

    if action == "deny":
        await query.edit_message_text(f"❌ Denied: `{tool_name}`", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⛔ Action `{tool_name}` was denied.",
                parse_mode="Markdown",
            )
        except Exception:
            pass
        return

    # Approved — execute the tool
    await query.edit_message_text(f"✅ Approved: `{tool_name}` — executing...", parse_mode="Markdown")
    try:
        result = await _dispatch_tool(tool_name, tool_args, user_id)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ `{tool_name}` complete:\n{result}",
            parse_mode="Markdown",
        )
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⚠️ `{tool_name}` failed after approval: {e}",
            parse_mode="Markdown",
        )

def get_session_history(user_id: str) -> deque:
    if user_id not in _session_history:
        _session_history[user_id] = deque(maxlen=20)
    return _session_history[user_id]

def append_to_history(user_id: str, role: str, content: str) -> None:
    get_session_history(user_id).append({"role": role, "content": content})

# ---------------------------------------------------------------------------
# Tool definitions — OpenAI tools format for llama-server
# ---------------------------------------------------------------------------
MIKO_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "task_add",
            "description": "Add a new task to the Koven Labs task board in master-postgres.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title":    {"type": "string", "description": "Short task title"},
                    "owner":    {"type": "string", "enum": ["eric", "david", "both"], "description": "Who owns this task"},
                    "priority": {"type": "integer", "enum": [1, 2, 3], "description": "1=critical, 2=normal, 3=low"},
                    "due_date": {"type": "string", "description": "ISO date string YYYY-MM-DD, optional"},
                },
                "required": ["title", "owner", "priority"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_update",
            "description": "Update the status of an existing task by ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id":     {"type": "integer", "description": "Task ID from the tasks table"},
                    "status": {"type": "string", "enum": ["open", "in_progress", "done", "blocked"]},
                },
                "required": ["id", "status"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "task_list",
            "description": "List open tasks for a principal. Returns up to 15 open tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "owner": {"type": "string", "enum": ["eric", "david", "both", "all"], "description": "Filter by owner. Use 'all' to see everything."},
                },
                "required": ["owner"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Permanently store a fact about the user or business into long-term memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string", "description": "The fact to remember, stated clearly and concisely."},
                },
                "required": ["fact"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "infra_query",
            "description": "Query live infrastructure state from master-postgres. Returns service health, uptime, and details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "Service name to filter, or 'all' for full state."},
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "Execute a read-only shell command on the node. Allowed: docker ps, docker logs, df, free, cat of log files, systemctl status. No writes, no rm, no curl to external URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to run."},
                },
                "required": ["command"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Tool executor — maps tool name → async Python function
# ---------------------------------------------------------------------------

async def _tool_task_add(title: str, owner: str, priority: int, due_date: str | None = None) -> str:
    if not ASYNCPG_AVAILABLE:
        return "Error: asyncpg not available"
    try:
        conn = await _asyncpg.connect(dsn=MASTER_POSTGRES_DSN)
        row = await conn.fetchrow(
            """INSERT INTO tasks (owner, title, priority, status, due_date)
               VALUES ($1, $2, $3, 'open', $4) RETURNING id""",
            owner, title, priority, due_date
        )
        await conn.close()
        return f"✅ Task #{row['id']} created: '{title}' [{owner}] priority {priority}"
    except Exception as e:
        return f"Error creating task: {e}"


async def _tool_task_update(id: int, status: str) -> str:
    if not ASYNCPG_AVAILABLE:
        return "Error: asyncpg not available"
    try:
        conn = await _asyncpg.connect(dsn=MASTER_POSTGRES_DSN)
        row = await conn.fetchrow(
            "UPDATE tasks SET status=$1, updated_at=NOW() WHERE id=$2 RETURNING id, title",
            status, id
        )
        await conn.close()
        if row:
            return f"✅ Task #{row['id']} '{row['title']}' → {status}"
        return f"No task found with id={id}"
    except Exception as e:
        return f"Error updating task: {e}"


async def _tool_task_list(owner: str) -> str:
    if not ASYNCPG_AVAILABLE:
        return "Error: asyncpg not available"
    try:
        conn = await _asyncpg.connect(dsn=MASTER_POSTGRES_DSN)
        if owner == "all":
            rows = await conn.fetch(
                "SELECT id, owner, title, priority, status, due_date FROM tasks WHERE status != 'done' ORDER BY priority, owner LIMIT 15"
            )
        else:
            rows = await conn.fetch(
                "SELECT id, owner, title, priority, status, due_date FROM tasks WHERE (owner=$1 OR owner='both') AND status != 'done' ORDER BY priority LIMIT 15",
                owner
            )
        await conn.close()
        if not rows:
            return "No open tasks found."
        lines = []
        for r in rows:
            icon = "🔴" if r["priority"] == 1 else "🟡" if r["priority"] == 2 else "🔵"
            due = f" | due {r['due_date']}" if r["due_date"] else ""
            lines.append(f"{icon} #{r['id']} [{r['owner']}] {r['title']} ({r['status']}){due}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing tasks: {e}"


async def _tool_remember(user_id: str, fact: str) -> str:
    success = await remember_fact(user_id=user_id, fact=fact)
    return f"✅ Remembered: {fact}" if success else "⚠️ Failed to write to memory"


async def _tool_infra_query(service: str) -> str:
    try:
        conn = await _asyncpg.connect(dsn=MASTER_POSTGRES_DSN)
        if service == "all":
            rows = await conn.fetch(
                "SELECT service, status, detail, checked_at FROM infrastructure_state ORDER BY service"
            )
        else:
            rows = await conn.fetch(
                "SELECT service, status, detail, checked_at FROM infrastructure_state WHERE service ILIKE $1",
                f"%{service}%"
            )
        await conn.close()
        if not rows:
            return f"No infrastructure data found for '{service}'"
        lines = []
        for r in rows:
            icon = "✅" if r["status"] == "ok" else "🔴"
            detail = f" — {r['detail']}" if r["detail"] else ""
            lines.append(f"{icon} {r['service']}{detail}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error querying infra: {e}"


SHELL_ALLOWED_PREFIXES = [
    "docker ps", "docker logs", "docker stats",
    "df ", "df\n", "free ", "free\n",
    "systemctl status",
    "cat /var/log", "cat /home/miko_node_001",
    "ls ", "pwd", "uptime", "uname",
    "curl http://172.", "curl http://localhost",
]

def _shell_allowed(command: str) -> bool:
    cmd = command.strip().lower()
    # Block dangerous ops
    for blocked in ["rm ", "sudo ", "chmod ", "chown ", "curl http", "wget ", "> /", "dd ", "mkfs"]:
        if blocked in cmd:
            return False
    for prefix in SHELL_ALLOWED_PREFIXES:
        if cmd.startswith(prefix.lower()):
            return True
    return False


async def _tool_shell_exec(command: str) -> str:
    if not _shell_allowed(command):
        return f"⛔ Command not permitted: `{command}`. Allowed: docker ps/logs, df, free, systemctl status, cat logs."
    try:
        result = subprocess.run(
            shlex.split(command),
            capture_output=True, text=True, timeout=10
        )
        out = result.stdout.strip() or result.stderr.strip()
        return out[:2000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "⚠️ Command timed out (10s limit)"
    except Exception as e:
        return f"Error: {e}"


async def _dispatch_tool(tool_name: str, tool_args: dict, user_id: str) -> str:
    """Execute a tool directly, no permission check."""
    if tool_name == "task_add":
        return await _tool_task_add(**tool_args)
    elif tool_name == "task_update":
        return await _tool_task_update(**tool_args)
    elif tool_name == "task_list":
        return await _tool_task_list(**tool_args)
    elif tool_name == "remember":
        return await _tool_remember(user_id=user_id, fact=tool_args.get("fact", ""))
    elif tool_name == "infra_query":
        return await _tool_infra_query(**tool_args)
    elif tool_name == "shell_exec":
        return await _tool_shell_exec(**tool_args)
    else:
        return f"Unknown tool: {tool_name}"


async def execute_tool(tool_name: str, tool_args: dict, user_id: str, bot=None) -> str:
    """
    Dispatch tool call with permission gating.
    Green: execute immediately.
    Yellow/Red: send approval request to Eric, store pending action,
                return a holding message immediately — callback handler
                executes the tool and sends the result when approved.
    """
    _log = logging.getLogger("miko.tools")
    _log.info("Executing tool: %s args=%s", tool_name, tool_args)

    tier = TOOL_PERMISSION_TIER.get(tool_name, "yellow")
    if tool_name == "shell_exec":
        cmd = tool_args.get("command", "").lower()
        if any(p in cmd for p in ["restart", "stop", "start", "kill", "rm ", "write", "chmod"]):
            tier = "red"

    # Green tier — execute immediately, no approval needed
    if tier == "green" or bot is None:
        try:
            return await _dispatch_tool(tool_name, tool_args, user_id)
        except Exception as e:
            _log.error("Tool %s failed: %s", tool_name, e)
            return f"Tool error: {e}"

    # Yellow/Red — send approval request, store pending, return immediately
    approval_id = str(uuid.uuid4())[:8]
    _pending_approvals[approval_id] = {
        "tool_name": tool_name,
        "tool_args": tool_args,
        "user_id": user_id,
        "chat_id": ERIC_CHAT_ID if user_id == "eric" else DAVID_CHAT_ID,
        "approved": None,
    }

    args_display = ", ".join(f"{k}={repr(v)}" for k, v in tool_args.items())
    timeout_note = "\n_Auto-approves in 2 min._" if tier == "yellow" else ""

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"approve:{approval_id}"),
        InlineKeyboardButton("❌ Deny",    callback_data=f"deny:{approval_id}"),
    ]])

    try:
        await bot.send_message(
            chat_id=ERIC_CHAT_ID,
            text=(
                f"🔐 *Permission Request* `[{approval_id}]`\n\n"
                f"Tool: `{tool_name}`\n"
                f"Args: `{args_display}`\n"
                f"From: {user_id}{timeout_note}"
            ),
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
    except Exception as e:
        _log.warning("Permission request send failed: %s", e)
        # Fail open for yellow
        if tier == "yellow":
            del _pending_approvals[approval_id]
            return await _dispatch_tool(tool_name, tool_args, user_id)
        return f"⛔ Could not send permission request for `{tool_name}`"

    # Schedule auto-approve for yellow after 120s
    if tier == "yellow":
        async def _auto_approve():
            await asyncio.sleep(120)
            if approval_id in _pending_approvals:
                entry = _pending_approvals.pop(approval_id)
                _log.info("Auto-approving %s after timeout", tool_name)
                result = await _dispatch_tool(entry["tool_name"], entry["tool_args"], entry["user_id"])
                try:
                    await bot.send_message(
                        chat_id=entry["chat_id"],
                        text=f"⏱ Auto-approved `{tool_name}`:\n{result}",
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass
        asyncio.create_task(_auto_approve())

    return f"⏳ Waiting for approval `[{approval_id}]` — you'll get a confirmation once approved."

MASTER_POSTGRES_DSN = os.getenv("MASTER_POSTGRES_DSN", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("miko")

# ---------------------------------------------------------------------------
# SOUL.md loader
# ---------------------------------------------------------------------------

def load_soul() -> str:
    if SOUL_MD_PATH.exists():
        return SOUL_MD_PATH.read_text()
    return "You are Miko, a sharp personal assistant for Eric at Miko Labs."

SOUL = load_soul()

# Telegram bot instance — set during startup so tools can send permission requests
_tg_bot = None

def set_tg_bot(bot) -> None:
    global _tg_bot
    _tg_bot = bot

# ---------------------------------------------------------------------------
# Model router — zero latency keyword classifier
# ---------------------------------------------------------------------------

_FAST_PATTERNS = [
    r"^(hey|hi|hello|sup|yo|morning|good morning|good night|gm|gn)[\s!?.]*$",
    r"\b(status|up|down|running|health|alive|online)\b",
    r"^(is|are|was|were|did|do|does|can|will|has|have)\s.{0,60}\?$",
    r"\b(infrastructure|services|containers|node|server|postgres|redis|qdrant)\b",
    r"^.{0,40}$",
    r"^(ok|okay|got it|thanks|thank you|cool|nice|perfect|great|sounds good|makes sense)[\s!.]*$",
    r"^(what time|what.s the time|what.s today|what day|who is|what is miko|what port).*$",
]

_DEEP_PATTERNS = [
    r"\b(strategy|brainstorm|think|analyze|compare|should i|help me|design|architecture|plan|roadmap|decide|tradeoff|recommend|advice|why|explain|how do|how should|what do you think)\b",
    r"\b(build|implement|code|write|create|draft|generate|debug|fix|refactor)\b",
    r"\b(pleadly|awaas|koven|client|outreach|pipeline|revenue|post-[abcde])\b",
    r".{200,}",
]

import re as _re

def route_model(message: str) -> tuple[str, str]:
    """Returns (model_name, server_url) based on message complexity."""
    msg = message.lower().strip()
    for pattern in _DEEP_PATTERNS:
        if _re.search(pattern, msg, _re.IGNORECASE):
            return MODEL, LLAMA_SERVER_URL
    for pattern in _FAST_PATTERNS:
        if _re.search(pattern, msg, _re.IGNORECASE):
            return FAST_MODEL, FAST_SERVER_URL
    return MODEL, LLAMA_SERVER_URL

# ---------------------------------------------------------------------------
# Infrastructure state query — reads from master-postgres
# ---------------------------------------------------------------------------

async def get_infrastructure_state() -> str:
    """Query infrastructure_state table and return formatted status block."""
    if not MASTER_POSTGRES_DSN or not ASYNCPG_AVAILABLE:
        return "Infrastructure state unavailable — no postgres connection configured."
    try:
        conn = await _asyncpg.connect(MASTER_POSTGRES_DSN)
        rows = await conn.fetch("""
            SELECT service, status, last_checked,
                   EXTRACT(EPOCH FROM (NOW() - last_checked))::int AS age_seconds,
                   detail
            FROM infrastructure_state
            ORDER BY project_id NULLS LAST, service
        """)
        await conn.close()

        if not rows:
            return "No infrastructure state data available yet."

        now = datetime.now(timezone.utc)
        lines = [f"Infrastructure state (as of {now.strftime('%H:%M UTC')}):"]
        all_ok = all(r["status"] == "ok" for r in rows)

        for r in rows:
            age = r["age_seconds"]
            if age < 60:
                age_str = f"{age}s ago"
            elif age < 3600:
                age_str = f"{age // 60}m ago"
            else:
                age_str = f"{age // 3600}h ago"

            icon = "✅" if r["status"] == "ok" else "🔴"
            lines.append(f"  {icon} {r['service']}: {r['status']} (checked {age_str})")

        if all_ok:
            lines.append(f"\nAll {len(rows)} services operational.")
        else:
            down = [r["service"] for r in rows if r["status"] != "ok"]
            lines.append(f"\n⚠️ Degraded: {', '.join(down)}")

        return "\n".join(lines)
    except Exception as e:
        logger.warning("Infrastructure state query failed: %s", e)
        return f"Could not reach infrastructure state database: {e}"

# ---------------------------------------------------------------------------
# Mem0 memory layer
# ---------------------------------------------------------------------------

try:
    from mem0 import Memory
    from mem0.configs.base import MemoryConfig

    from qdrant_client import QdrantClient as _QdrantClient
    _qdrant_host = QDRANT_URL.replace("http://", "").split(":")[0]
    _qdrant_port = int(QDRANT_URL.split(":")[-1])
    _qdrant_client = _QdrantClient(
        host=_qdrant_host,
        port=_qdrant_port,
        api_key=QDRANT_API_KEY,
        https=False,
    )

    _mem0_config = MemoryConfig(
        vector_store={
            "provider": "qdrant",
            "config": {
                "client": _qdrant_client,
                "collection_name": "miko_memory",
                "embedding_model_dims": 768,
            },
        },
        llm={
            "provider": "openai",
            "config": {
                "model": MODEL,
                "openai_base_url": f"{LLAMA_SERVER_URL}/v1",
                "api_key": "not-needed",
                "temperature": 0.1,
                "max_tokens": 2000,
            },
        },
        embedder={
            "provider": "openai",
            "config": {
                "model": "nomic-embed-text",
                "openai_base_url": f"{EMBED_SERVER_URL}/v1",
                "api_key": "not-needed",
                "embedding_dims": 768,
            },
        },
        version="v1.1",
    )
    _memory = Memory(config=_mem0_config)
    MEMORY_AVAILABLE = True
    logger.info("Mem0 memory initialized — Qdrant @ %s, embedder: llama-server", QDRANT_URL)
except Exception as e:
    _memory = None
    MEMORY_AVAILABLE = False
    logger.warning("Mem0 unavailable: %s", e)


async def get_memories(user_id: str, query: str, limit: int = 6) -> list[str]:
    """Retrieve relevant memories via Mem0 semantic search."""
    if not MEMORY_AVAILABLE or _memory is None:
        return []
    try:
        results = await asyncio.get_event_loop().run_in_executor(None, lambda: _memory.search(query=query, user_id=user_id, limit=limit))
        if isinstance(results, list):
            return [r["memory"] for r in results if "memory" in r]
        return [r["memory"] for r in results.get("results", [])]
    except Exception as e:
        logger.warning("Memory retrieval failed: %s", e)
        return []


async def remember_fact(user_id: str, fact: str) -> bool:
    """Write a fact directly to Qdrant, bypassing Mem0 LLM extraction.
    Used for explicit Remember: commands — deterministic, never fails due to LLM."""
    if not MEMORY_AVAILABLE or _memory is None:
        return False
    try:
        import uuid, time
        # Get embedding for the fact
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{EMBED_SERVER_URL}/v1/embeddings",
                json={"model": EMBED_MODEL, "input": fact}
            )
            resp.raise_for_status()
            vector = resp.json()["data"][0]["embedding"]

        point_id = str(uuid.uuid4())
        payload = {
            "memory": fact,
            "data": fact,
            "user_id": user_id,
            "created_at": int(time.time()),
            "source": "explicit_remember",
        }

        # Write directly to Qdrant
        qdrant_url = str(_memory.vector_store.client._client.base_url).rstrip("/")
        api_key = _memory.vector_store.client.api_key or ""
        headers = {"api-key": api_key, "Content-Type": "application/json"} if api_key else {"Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.put(
                f"{qdrant_url}/collections/miko_memory/points?wait=true",
                headers=headers,
                json={"points": [{"id": point_id, "vector": vector, "payload": payload}]}
            )
            resp.raise_for_status()

        logger.info("remember_fact: wrote '%s' for %s", fact[:60], user_id)
        return True
    except Exception as e:
        logger.warning("remember_fact failed: %s", e)
        return False


async def save_memory(user_id: str, messages: list[dict]) -> None:
    """Extract and save facts from conversation turn via Mem0."""
    if not MEMORY_AVAILABLE or _memory is None:
        return
    for attempt in range(3):
        try:
            await asyncio.get_event_loop().run_in_executor(None, lambda: _memory.add(messages=messages, user_id=user_id))
            return
        except Exception as e:
            if attempt < 2:
                logger.warning("Memory save attempt %d failed: %s — retrying", attempt + 1, e)
                await asyncio.sleep(1.0)
            else:
                logger.warning("Memory save failed after 3 attempts: %s", e)

# ---------------------------------------------------------------------------
# Pleadly status
# ---------------------------------------------------------------------------

async def get_pleadly_status() -> dict[str, Any]:
    """Hit Pleadly /health and /spend endpoints and return structured status."""
    status: dict[str, Any] = {
        "health": None,
        "spend": None,
        "reachable": False,
        "error": None,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health_resp = await client.get(f"{PLEADLY_API_URL}/health")
            if health_resp.status_code == 200:
                status["health"] = health_resp.json()
                status["reachable"] = True

            spend_resp = await client.get(f"{PLEADLY_API_URL}/spend")
            if spend_resp.status_code == 200:
                status["spend"] = spend_resp.json()
    except Exception as e:
        status["error"] = str(e)
    return status


def format_pleadly_status(status: dict[str, Any]) -> str:
    """Format Pleadly status into a readable string for context injection."""
    if not status["reachable"]:
        return f"Pleadly API is UNREACHABLE. Error: {status.get('error', 'unknown')}"

    lines = ["Pleadly API: online"]

    if h := status.get("health"):
        if isinstance(h, dict):
            for k, v in h.items():
                lines.append(f"  {k}: {v}")

    if s := status.get("spend"):
        if isinstance(s, dict):
            lines.append("Spend today:")
            for k, v in s.items():
                lines.append(f"  {k}: {v}")

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

async def chat_with_miko(
    user_message: str,
    user_id: str,
    include_pleadly_status: bool = False,
) -> str:
    """
    Core reasoning call — Layer 1 agent loop.

    Architecture:
    1. Build system prompt: SOUL + principal context + datetime + memories + infra
    2. Inject session history for conversational continuity
    3. Call 35B with tools array (Hermes-style tool calling)
    4. If model requests a tool call: execute it, inject result, re-call for final response
    5. Save exchange to session history + Mem0
    6. Max 5 tool call iterations per message to prevent runaway loops
    """
    # ── Build system prompt ────────────────────────────────────────────────
    system_parts = [SOUL]

    principal_ctx = get_principal_context(user_id)
    if principal_ctx:
        system_parts.append(principal_ctx)

    now = datetime.now(timezone.utc).strftime("%A, %B %d, %Y at %H:%M UTC")
    system_parts.append(f"\n\n---\nCurrent date/time: {now}")

    memories = await get_memories(user_id=user_id, query=user_message)
    if memories:
        mem_lines = "\n".join(f"- {m}" for m in memories[:5])
        system_parts.append(f"\n\n---\nWhat you remember about {user_id}:\n{mem_lines}")

    infra_keywords = ["pleadly", "status", "health", "pipeline", "api", "server", "running", "down",
                       "node", "infrastructure", "services", "containers", "docker", "llama", "miko",
                       "memory", "gpu", "ram", "queue", "up", "operational", "state", "task", "tasks"]
    if include_pleadly_status or any(k in user_message.lower() for k in infra_keywords):
        pleadly_status = await get_pleadly_status()
        status_str = format_pleadly_status(pleadly_status)
        infra_state = await get_infrastructure_state()
        system_parts.append(f"\n\n---\nLive system status (just fetched):\n{status_str}\n\n{infra_state}")

    system_parts.append("""

---
TOOL USE INSTRUCTIONS:
You have access to tools. Use them when the user asks you to take action — add tasks, check infra, remember facts, run shell commands.
When you call a tool, you MUST use this exact format with no text after the tool call:
<tool_call>
{"name": "tool_name", "arguments": {"arg1": "value1"}}
</tool_call>
After the tool result is returned, synthesize a natural response. Never fabricate tool results.""")

    system_prompt = "".join(system_parts)

    # ── Build message list with session history ────────────────────────────
    history = list(get_session_history(user_id))
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history[-14:])  # last 14 turns = ~7 exchanges
    messages.append({"role": "user", "content": user_message})

    # Always use deep model for tool-capable calls
    selected_model = MODEL
    selected_server = LLAMA_SERVER_URL
    logger.info("Agent call: model=%s", selected_model[:30])

    # ── Agent loop — max 5 tool call iterations ────────────────────────────
    final_reply = ""
    tool_iterations = 0
    MAX_TOOL_ITERATIONS = 5

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            while tool_iterations <= MAX_TOOL_ITERATIONS:
                response = await client.post(
                    f"{selected_server}/v1/chat/completions",
                    json={
                        "model": selected_model,
                        "messages": messages,
                        "tools": MIKO_TOOLS,
                        "tool_choice": "auto",
                        "stream": False,
                        "temperature": 0.7,
                        "max_tokens": 1024,
                    },
                )
                response.raise_for_status()
                data = response.json()
                choice = data["choices"][0]
                msg = choice["message"]
                content = msg.get("content") or ""
                tool_calls = msg.get("tool_calls") or []

                # ── Path A: native tool_calls in response ──────────────────
                if tool_calls:
                    tool_call = tool_calls[0]  # handle first tool call
                    tool_name = tool_call["function"]["name"]
                    try:
                        tool_args = json.loads(tool_call["function"]["arguments"])
                    except Exception:
                        tool_args = {}

                    logger.info("Tool call (native): %s %s", tool_name, tool_args)
                    tool_result = await execute_tool(tool_name, tool_args, user_id, bot=_tg_bot)

                    # If tool is pending approval, return holding message immediately
                    if tool_result.startswith("⏳ Waiting for approval"):
                        final_reply = tool_result
                        break

                    # Append assistant tool call + tool result to messages
                    messages.append(msg)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.get("id", "call_0"),
                        "content": tool_result,
                    })
                    tool_iterations += 1
                    continue

                # ── Path B: XML <tool_call> in content (Hermes fallback) ───
                xml_match = re.search(r"<tool_call>\s*({.*?})\s*</tool_call>", content, re.DOTALL)
                if xml_match:
                    try:
                        call_data = json.loads(xml_match.group(1))
                        tool_name = call_data.get("name", "")
                        tool_args = call_data.get("arguments", {})
                        logger.info("Tool call (XML): %s %s", tool_name, tool_args)
                        tool_result = await execute_tool(tool_name, tool_args, user_id, bot=_tg_bot)

                        if tool_result.startswith("⏳ Waiting for approval"):
                            final_reply = tool_result
                            break
                        messages.append({"role": "assistant", "content": content})
                        messages.append({"role": "user", "content": f"Tool result: {tool_result}"})
                        tool_iterations += 1
                        continue
                    except Exception as e:
                        logger.warning("XML tool parse failed: %s", e)

                # ── Path C: no tool call — final response ──────────────────
                final_reply = content.strip()
                break

            else:
                # Hit iteration cap — return last content
                final_reply = content.strip() if content else "I hit my action limit on that request. Ask me to continue."

    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return "Something broke on my end. Check the logs — I'll be back in a second."

    # ── Save to session history + Mem0 ────────────────────────────────────
    append_to_history(user_id, "user", user_message)
    append_to_history(user_id, "assistant", final_reply)

    asyncio.create_task(save_memory(
        user_id=user_id,
        messages=[
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": final_reply},
        ],
    ))

    return final_reply

# ---------------------------------------------------------------------------
# Telegram bot
# ---------------------------------------------------------------------------

def get_user_id(chat_id: int) -> str:
    """Map Telegram chat_id to memory namespace."""
    if chat_id == ERIC_CHAT_ID:
        return "eric"
    if chat_id == DAVID_CHAT_ID:
        return "david"
    return f"user_{chat_id}"


def get_principal_context(user_id: str) -> str:
    """Return principal-specific context block injected into system prompt."""
    if user_id == "eric":
        return """
---
PRINCIPAL: Eric (Technical Co-Founder)
You are talking to Eric. He owns infrastructure, model stack, agent fleet, and security.
- Call him Boss when it fits naturally
- He thinks in systems and leverage points
- Lead with decisions, then detail
- He is comfortable with full technical depth
- Current focus: Pleadly intelligence plane, POST-A/B activation, Node 1 stability
- Kill condition awareness: first paid Pleadly client before anything downstream unlocks
- G2 cleared March 9 — outreach is now unblocked, David owns execution
"""
    if user_id == "david":
        return """
---
PRINCIPAL: David (Sales & Delivery Co-Founder, @genkitools)
You are talking to David. He owns client acquisition, sales, outreach, and delivery.
- He does not need technical depth unless he asks for it
- His world is: pipeline, conversations, proposals, client relationships
- Lead with what's relevant to revenue and relationships
- Current focus: Pleadly outreach execution, Clay ICP build, Smartlead sequences, first pilot close
- He has equal authority to Eric on all business decisions
- His domain is final: client relationships, outreach strategy, proposal terms
- Miko supports him the same way she supports Eric — sharp, direct, no filler
- Do NOT reference infrastructure details unless he specifically asks
- Surface anything that affects his pipeline, his outreach, or his client health
"""
    return ""


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming Telegram messages."""
    if not update.message or not update.message.text:
        return

    chat_id = update.effective_chat.id
    user_id = get_user_id(chat_id)
    user_message = update.message.text.strip()

    logger.info("Message from %s (user_id=%s): %s", chat_id, user_id, user_message[:80])

    # Typing indicator
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # Intercept explicit Remember: commands — write directly, skip LLM extraction
    if user_message.lower().startswith("remember:"):
        fact = user_message[9:].strip().strip('"')
        success = await remember_fact(user_id=user_id, fact=fact)
        if success:
            reply = f"✓ Logged: {fact}"
        else:
            reply = "⚠️ Failed to write that to memory — check logs."
    else:
        reply = await chat_with_miko(user_message=user_message, user_id=user_id)

    # Telegram has 4096 char limit — split if needed
    if len(reply) <= 4096:
        await update.message.reply_text(reply)
    else:
        chunks = [reply[i:i+4096] for i in range(0, len(reply), 4096)]
        for chunk in chunks:
            await update.message.reply_text(chunk)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — live infrastructure state from master-postgres."""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    status = await get_pleadly_status()
    status_str = format_pleadly_status(status)
    infra_state = await get_infrastructure_state()

    chat_id = update.effective_chat.id
    user_id = get_user_id(chat_id)

    reply = await chat_with_miko(
        user_message=f"Give me a status summary. Here's the live data:\n{status_str}\n\n{infra_state}",
        user_id=user_id,
        include_pleadly_status=False,
    )
    await update.message.reply_text(reply)


async def cmd_memory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/memory — show what Miko remembers about this user."""
    chat_id = update.effective_chat.id
    user_id = get_user_id(chat_id)
    memories = await get_memories(user_id=user_id, query="everything", limit=10)
    if not memories:
        await update.message.reply_text("No memories stored yet. Talk to me for a bit and I'll start building context.")
        return
    memory_text = "\n".join(f"{i+1}. {m}" for i, m in enumerate(memories))
    await update.message.reply_text(f"What I remember about you:\n\n{memory_text}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — command list."""
    text = (
        "Commands:\n"
        "/status — live Pleadly pipeline status\n"
        "/memory — what I remember about you\n"
        "/help — this list\n\n"
        "Or just talk to me. I'm here."
    )
    await update.message.reply_text(text)


def build_telegram_app() -> Application:
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("memory", cmd_memory))
    app.add_handler(CallbackQueryHandler(handle_approval_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    set_tg_bot(app.bot)
    return app

# ---------------------------------------------------------------------------
# FastAPI web UI backend
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    user_id: str = "eric"
    include_status: bool = False


class ChatResponse(BaseModel):
    reply: str
    memories_used: int
    timestamp: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Miko web backend starting")
    yield
    logger.info("Miko web backend stopping")


web_app = FastAPI(title="Miko", lifespan=lifespan)
web_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@web_app.post("/api/chat", response_model=ChatResponse)
async def api_chat(req: ChatRequest):
    memories = await get_memories(user_id=req.user_id, query=req.message)
    reply = await chat_with_miko(
        user_message=req.message,
        user_id=req.user_id,
        include_pleadly_status=req.include_status,
    )
    return ChatResponse(
        reply=reply,
        memories_used=len(memories),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@web_app.get("/api/status")
async def api_status():
    status = await get_pleadly_status()
    return status


@web_app.get("/api/memories/{user_id}")
async def api_memories(user_id: str, query: str = "everything"):
    memories = await get_memories(user_id=user_id, query=query, limit=10)
    return {"user_id": user_id, "memories": memories, "count": len(memories)}


@web_app.get("/health")
async def health():
    return {"status": "ok", "memory": MEMORY_AVAILABLE, "soul_loaded": bool(SOUL)}

# ---------------------------------------------------------------------------
# Entrypoint — runs both Telegram bot and FastAPI concurrently
# ---------------------------------------------------------------------------

async def main():
    if not TELEGRAM_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram bot disabled")
        import uvicorn
        config = uvicorn.Config(web_app, host="0.0.0.0", port=8400, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()
        return

    # Build telegram app
    tg_app = build_telegram_app()

    # Run both concurrently
    import uvicorn
    config = uvicorn.Config(web_app, host="0.0.0.0", port=8400, log_level="info")
    server = uvicorn.Server(config)

    async with tg_app:
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)
        logger.info("Miko Telegram bot started")
        logger.info("Miko web backend starting on :8400")
        await server.serve()
        await tg_app.updater.stop()
        await tg_app.stop()


if __name__ == "__main__":
    asyncio.run(main())
