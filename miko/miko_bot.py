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
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
N8N_URL          = os.getenv("N8N_URL",            "http://awaas-n8n:5679")

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
    "web_fetch":   "green",
    "web_search":  "green",
    "claude_query":"green",
    "research":    "green",
    "task_add":    "yellow",
    "task_update": "yellow",
    "shell_exec":  "yellow",
    "write_file":  "yellow",
    "git_status":  "green",
    "read_file":   "green",
    "git_commit":  "red",
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
        await query.edit_message_text(f"Denied: {tool_name}")
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Action {tool_name} was denied.",
            )
        except Exception:
            pass
        return

    # Approved — execute the tool
    await query.edit_message_text(f"Approved: {tool_name} — executing...")
    try:
        result = await _dispatch_tool(tool_name, tool_args, user_id)

        # ── Resume agent loop if we have saved context ─────────────────
        saved = _pending_contexts.pop(approval_id, None)
        if saved:
            # Inject real tool result into saved messages
            resume_messages = saved["messages"]
            for m in reversed(resume_messages):
                if m.get("content") == "__PENDING__":
                    m["content"] = result
                    break
            # Re-enter chat_with_miko synthesis — skip tool calls, just synthesize
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                synthesis = await _resume_after_approval(
                    messages=resume_messages,
                    user_id=saved["user_id"],
                    user_message=saved["user_message"],
                )
                if synthesis:
                    safe_synth = (synthesis
                        .replace("*","").replace("`","'")
                        .replace("_"," ").replace("[","(").replace("]",")")
                    )[:4000]
                    await context.bot.send_message(chat_id=chat_id, text=safe_synth)
                    return  # synthesis sent, skip raw result dump
            except Exception as se:
                logger.warning("Resume synthesis failed: %s", se)
                # Fall through to raw result dump

        # Fallback: send raw result if no context or synthesis failed
        safe = (result
            .replace("*","").replace("`","'")
            .replace("_"," ").replace("[","(").replace("]",")")
        )[:3000]
        header = f"Tool: {tool_name}\n\n"
        full = header + safe
        if len(full) <= 4096:
            await context.bot.send_message(chat_id=chat_id, text=full)
        else:
            chunks = [full[i:i+3900] for i in range(0, len(full), 3900)]
            for i, chunk in enumerate(chunks[:3]):
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=chunk if i == 0 else f"[cont {i+1}] " + chunk,
                )
    except Exception as e:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Tool {tool_name} failed: {str(e)[:300]}",
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
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch and extract clean text content from any URL. Use for reading articles, docs, competitor pages, news, GitHub repos, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url":      {"type": "string", "description": "Full URL to fetch"},
                    "summary":  {"type": "boolean", "description": "If true, return a brief summary instead of full text. Default false."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for current information. Returns top results with titles, URLs, and snippets. Use before web_fetch to find the right URLs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string", "description": "Search query"},
                    "max_results": {"type": "integer", "description": "Number of results to return. Default 5, max 10."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "claude_query",
            "description": "Consult Claude (claude-sonnet-4-20250514) for tasks requiring deep analysis, very long documents, complex reasoning across many sources, or when you need a second opinion on high-stakes decisions. Use sparingly — it costs money.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt":   {"type": "string", "description": "The full prompt to send to Claude"},
                    "context":  {"type": "string", "description": "Additional context or document text to include"},
                    "max_tokens": {"type": "integer", "description": "Max tokens for response. Default 2000."},
                },
                "required": ["prompt"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "research",
            "description": "Run an autonomous multi-step research task in the background. Miko will search, fetch, synthesize, and return a full intelligence brief. Use for competitive research, market analysis, technical deep-dives. Runs async — you get pinged when done.",
            "parameters": {
                "type": "object",
                "properties": {
                    "goal":    {"type": "string", "description": "What to research — be specific about what you want to know"},
                    "depth":   {"type": "string", "enum": ["quick", "standard", "deep"], "description": "quick=3 sources, standard=8 sources, deep=15+ sources"},
                },
                "required": ["goal"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Restricted to /workspace and known config/log paths under /awaas. Use to inspect code, configs, logs before making changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute path to file"},
                    "lines": {"type": "integer", "description": "Max lines to return. Default 200."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or overwrite a file. ONLY allowed in /workspace directory. Use for creating scripts, configs, drafts. Never writes outside /workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path":    {"type": "string", "description": "Absolute path under /workspace/"},
                    "content": {"type": "string", "description": "Full file content to write"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Run git status and git diff --stat in a repository. Read-only. Use before proposing changes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repo path. Options: miko, pleadly, miko-infra. Default: miko-infra"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Stage all changes and commit in a repository. RED tier — always requires explicit approval. Shows diff before committing.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo":    {"type": "string", "description": "Repo path. Options: miko, pleadly, miko-infra"},
                    "message": {"type": "string", "description": "Commit message"},
                },
                "required": ["repo", "message"],
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


# ---------------------------------------------------------------------------
# Web + AI tool implementations
# ---------------------------------------------------------------------------

def _safe_tg(text: str, max_len: int = 4000) -> str:
    """Strip characters that break Telegram's text parser."""
    return (text
        .replace("*", "")
        .replace("`", "'")
        .replace("_", " ")
        .replace("[", "(")
        .replace("]", ")")
    )[:max_len]


async def _send_typing(chat_id: int, duration: float = 0):
    """Send typing action. If duration > 0, keep sending every 4s for that many seconds."""
    if not _tg_bot:
        return
    if duration <= 0:
        try:
            await _tg_bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass
        return
    async def _loop():
        elapsed = 0.0
        while elapsed < duration:
            try:
                await _tg_bot.send_chat_action(chat_id=chat_id, action="typing")
            except Exception:
                pass
            await asyncio.sleep(4.0)
            elapsed += 4.0
    asyncio.create_task(_loop())

async def _tool_web_fetch(url: str, summary: bool = False) -> str:
    """Fetch and extract clean text from a URL using trafilatura."""
    try:
        import trafilatura
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MikoBot/1.0)"}) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
            favor_recall=True,
        )

        if not text:
            # Fallback: BeautifulSoup plain text
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)[:8000]

        if not text:
            return f"Could not extract text from {url}"

        text = text[:12000]  # cap at ~3K tokens

        if summary and len(text) > 1000:
            # Use local 35B to summarize
            async with httpx.AsyncClient(timeout=60.0) as client:
                r = await client.post(
                    f"{LLAMA_SERVER_URL}/v1/chat/completions",
                    json={
                        "model": MODEL,
                        "messages": [
                            {"role": "system", "content": "Summarize the following web page content in 3-5 bullet points. Be concise and factual."},
                            {"role": "user", "content": text},
                        ],
                        "max_tokens": 400,
                        "temperature": 0.3,
                    }
                )
                return r.json()["choices"][0]["message"]["content"].strip()

        return f"[{url}]\n\n{text}"

    except Exception as e:
        return f"web_fetch error: {e}"


async def _tool_web_search(query: str, max_results: int = 5) -> str:
    """Search the web via DuckDuckGo HTML (no API key required)."""
    try:
        import random
        from bs4 import BeautifulSoup as _BS
        max_results = min(max_results, 10)
        encoded = query.replace(' ', '+')
        uas = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
        ]

        async def _try_ddg(q_encoded: str):
            for attempt in range(2):
                async with httpx.AsyncClient(timeout=12.0, follow_redirects=True,
                    headers={"User-Agent": random.choice(uas),
                             "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                             "Accept-Language": "en-US,en;q=0.5", "DNT": "1"}) as c:
                    r = await c.get(f"https://html.duckduckgo.com/html/?q={q_encoded}")
                if r.status_code == 200:
                    return r
                await asyncio.sleep(2.0)
            return None

        async def _try_brave(q_encoded: str):
            """Brave search HTML — no API key needed."""
            try:
                async with httpx.AsyncClient(timeout=12.0, follow_redirects=True,
                    headers={"User-Agent": random.choice(uas),
                             "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                             "Accept-Language": "en-US,en;q=0.5"}) as c:
                    r = await c.get(f"https://search.brave.com/search?q={q_encoded}&source=web")
                if r.status_code == 200:
                    return r
            except Exception:
                pass
            return None

        # Try DDG first, then Brave
        resp = await _try_ddg(encoded)
        backend = "ddg"
        if not resp:
            resp = await _try_brave(encoded)
            backend = "brave"
        if not resp:
            return f"web_search unavailable (all backends rate-limited) for: {query}"

        soup = _BS(resp.text, "lxml")
        results = []
        if backend == "ddg":
            for r in soup.select(".result")[:max_results]:
                title_el = r.select_one(".result__title")
                snippet_el = r.select_one(".result__snippet")
                url_el = r.select_one(".result__url")
                title = title_el.get_text(strip=True) if title_el else "No title"
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                url = url_el.get_text(strip=True) if url_el else ""
                results.append(f"**{title}**\n{url}\n{snippet}")
        else:  # brave — JS-rendered, extract URLs from cite tags
            seen = set()
            for r in soup.select(".snippet")[:max_results * 2]:
                url_el = r.select_one("cite")
                if not url_el:
                    continue
                raw_url = url_el.get_text(strip=True).replace(" ", "").replace("›", "/")
                # Reconstruct full URL
                if raw_url and not raw_url.startswith("http"):
                    raw_url = "https://" + raw_url
                if raw_url and raw_url not in seen:
                    seen.add(raw_url)
                    results.append(f"**{raw_url}**\n{raw_url}\n")
                if len(results) >= max_results:
                    break
        for r in soup.select(".result")[:max_results]:
            title_el = r.select_one(".result__title")
            snippet_el = r.select_one(".result__snippet")
            url_el = r.select_one(".result__url")
            title = title_el.get_text(strip=True) if title_el else "No title"
            snippet = snippet_el.get_text(strip=True) if snippet_el else ""
            url = url_el.get_text(strip=True) if url_el else ""
            results.append(f"**{title}**\n{url}\n{snippet}")

        if not results:
            return f"No results found for: {query}"

        return f"Search results for '{query}':\n\n" + "\n\n---\n\n".join(results)

    except Exception as e:
        return f"web_search error: {e}"


async def _tool_claude_query(prompt: str, context: str = "", max_tokens: int = 2000) -> str:
    """Query Claude claude-sonnet-4-20250514 via Anthropic API."""
    if not ANTHROPIC_API_KEY:
        return "Error: ANTHROPIC_API_KEY not configured"
    try:
        full_prompt = f"{context}\n\n{prompt}".strip() if context else prompt
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-sonnet-4-20250514",
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": full_prompt}],
                }
            )
            resp.raise_for_status()
            return resp.json()["content"][0]["text"]
    except Exception as e:
        return f"claude_query error: {e}"


# Active research tasks: task_id → status
_research_tasks: dict[str, str] = {}

# Pending approval conversation contexts: approval_id → {messages, user_id, user_message}
_pending_contexts: dict[str, dict] = {}

async def _tool_research(goal: str, depth: str = "standard", user_id: str = "eric") -> str:
    """
    Spin up an async research loop. Returns immediately with a task ID.
    The loop runs in background: search → fetch → synthesize → notify.
    """
    task_id = str(uuid.uuid4())[:8]
    _research_tasks[task_id] = "running"

    source_counts = {"quick": 3, "standard": 6, "deep": 12}
    n_sources = source_counts.get(depth, 6)

    async def _run_research():
        try:
            logger = logging.getLogger("miko.research")
            logger.info("Research task %s started: %s", task_id, goal)
            chat_id = ERIC_CHAT_ID if user_id == "eric" else DAVID_CHAT_ID

            async def _status(msg: str):
                """Send a brief status update and typing indicator."""
                if _tg_bot:
                    try:
                        await _tg_bot.send_message(chat_id=chat_id, text=msg)
                        await _tg_bot.send_chat_action(chat_id=chat_id, action="typing")
                    except Exception:
                        pass

            await _status(f"🔍 Starting research [{task_id}]...")

            # Step 1: Decompose goal into 2-3 short search queries via fast model
            decompose_prompt = f"""Break this research goal into 2-3 short, specific web search queries (1-6 words each).
Return ONLY the queries, one per line, no numbering, no explanation.

Goal: {goal}"""
            async with httpx.AsyncClient(timeout=30.0) as _dc:
                _dr = await _dc.post(
                    f"{LLAMA_SERVER_URL}/v1/chat/completions",
                    json={
                        "model": MODEL,
                        "messages": [{"role": "user", "content": decompose_prompt}],
                        "max_tokens": 80,
                        "temperature": 0.2,
                    }
                )
                raw_queries = _dr.json()["choices"][0]["message"]["content"].strip()
            search_queries = [q.strip() for q in raw_queries.split("\n") if q.strip() and len(q.strip()) > 3][:3]
            if not search_queries:
                search_queries = [goal[:60]]
            logger.info("Research %s queries: %s", task_id, search_queries)
            await _status(f"🔎 Searching: {' | '.join(search_queries)}")

            # Step 2: Search each query, collect all results
            all_search_results = []
            urls = []
            for sq in search_queries:
                sr = await _tool_web_search(sq, max_results=max(2, n_sources // len(search_queries)))
                all_search_results.append(sr)
                for line in sr.split("\n"):
                    line = line.strip()
                    if line.startswith("http") and len(line) < 200:
                        urls.append(line)
                await asyncio.sleep(0.8)
            search_results = "\n\n".join(all_search_results)

            # Step 3: Inject known authoritative URLs based on goal keywords
            known_urls = []
            goal_lower = goal.lower()
            if "caseflood" in goal_lower:
                known_urls = [
                    "https://www.ycombinator.com/companies/caseflood-ai",
                    "https://caseflood.ai",
                    "https://www.linkedin.com/company/caseflood",
                ]
            elif "evenup" in goal_lower:
                known_urls = ["https://www.ycombinator.com/companies/evenup", "https://www.evenuplaw.com"]
            # Dedupe and prepend known URLs
            for ku in reversed(known_urls):
                if ku not in urls:
                    urls.insert(0, ku)

            # Step 4: Fetch and extract content from top URLs
            await _status(f"📄 Fetching {min(len(urls), n_sources)} sources...")
            fetched = []
            for url in urls[:n_sources]:
                text = await _tool_web_fetch(url, summary=True)
                if "error" not in text.lower() and "could not extract" not in text.lower():
                    fetched.append(f"Source: {url}\n{text}")
                await asyncio.sleep(0.5)  # polite crawl delay

            # Step 4: Synthesize with 35B
            if fetched:
                await _status(f"🧠 Synthesizing {len(fetched)} sources...")
                combined = "\n\n---\n\n".join(fetched[:n_sources])
                synthesis_prompt = f"""You are Miko, a strategic intelligence assistant for Koven Labs.

Research goal: {goal}

Sources gathered:
{combined[:16000]}

Produce a sharp intelligence brief with:
1. Key findings (3-5 bullets)
2. Strategic implications for Koven Labs / Pleadly
3. Action items if any
4. Sources used

Be direct, factual, and cut anything that isn't immediately useful."""

                async with httpx.AsyncClient(timeout=120.0) as client:
                    r = await client.post(
                        f"{LLAMA_SERVER_URL}/v1/chat/completions",
                        json={
                            "model": MODEL,
                            "messages": [
                                {"role": "system", "content": "You are a sharp strategic intelligence analyst."},
                                {"role": "user", "content": synthesis_prompt},
                            ],
                            "max_tokens": 1500,
                            "temperature": 0.4,
                        }
                    )
                    brief = r.json()["choices"][0]["message"]["content"].strip()
            else:
                brief = f"Could not fetch sources for: {goal}\n\nSearch results:\n{search_results}"

            _research_tasks[task_id] = "done"
            logger.info("Research task %s complete", task_id)

            # Notify via Telegram — plain text to avoid Markdown parse errors in LLM output
            if _tg_bot:
                header = f"🔬 Research Complete [{task_id}]\nGoal: {goal}\n\n"
                # Strip markdown symbols that break Telegram parser
                clean_brief = brief.replace("**", "").replace("__", "").replace("`", "'")
                full_msg = header + clean_brief
                # Telegram 4096 char limit — split if needed
                if len(full_msg) <= 4096:
                    await _tg_bot.send_message(chat_id=chat_id, text=full_msg)
                else:
                    chunks = [full_msg[i:i+4000] for i in range(0, min(len(full_msg), 12000), 4000)]
                    for i, chunk in enumerate(chunks):
                        prefix = "" if i == 0 else f"[{task_id} cont'd {i+1}]\n"
                        await _tg_bot.send_message(chat_id=chat_id, text=prefix + chunk)
                        await asyncio.sleep(0.3)

        except Exception as e:
            _research_tasks[task_id] = f"error: {e}"
            logging.getLogger("miko.research").error("Research task %s failed: %s", task_id, e)
            if _tg_bot:
                await _tg_bot.send_message(
                    chat_id=chat_id,
                    text=f"⚠️ Research task `{task_id}` failed: {e}",
                    parse_mode="Markdown",
                )

    asyncio.create_task(_run_research())
    return f"🔬 Research task `{task_id}` started ({depth}, ~{n_sources} sources).\nGoal: {goal}\n\nI\'ll ping you when the brief is ready."


# ---------------------------------------------------------------------------
# L4 — Autonomous Build Agent Tools
# ---------------------------------------------------------------------------

WORKSPACE_DIR = "/workspace"
REPO_PATHS = {
    "miko":      "/awaas/miko",
    "pleadly":   "/awaas/pleadly",
    "miko-infra": "/awaas",
}

def _safe_read_path(path: str) -> bool:
    """Return True if path is safe to read."""
    allowed_prefixes = [
        WORKSPACE_DIR,
        "/awaas/miko/miko_bot.py",
        "/awaas/miko/docker-compose.yml",
        "/awaas/miko/requirements.txt",
        "/awaas/miko/SOUL.md",
        "/awaas/orchestrator/conductor/main.py",
        "/awaas/orchestrator/conductor/settings.py",
        "/awaas/pleadly",
        "/pleadly-repo",
        "/awaas/.env.template",
        "/tmp/",
    ]
    return any(path.startswith(p) for p in allowed_prefixes)


async def _tool_read_file(path: str, lines: int = 200) -> str:
    """Read file contents, restricted to safe paths."""
    if not _safe_read_path(path):
        return f"Access denied: {path} is outside allowed read paths. Use /workspace/ or known config paths."
    try:
        import aiofiles
        async with aiofiles.open(path, "r", encoding="utf-8", errors="replace") as f:
            all_lines = await f.readlines()
        total = len(all_lines)
        selected = all_lines[:lines]
        result = "".join(selected)
        if total > lines:
            result += f"\n... [{total - lines} more lines]"
        return f"[{path}] ({total} lines total)\n\n{result}"
    except FileNotFoundError:
        return f"File not found: {path}"
    except Exception as e:
        return f"read_file error: {e}"


async def _tool_write_file(path: str, content: str) -> str:
    """Write file, strictly sandboxed to /workspace."""
    if not path.startswith(WORKSPACE_DIR):
        return f"Write denied: {path} is outside /workspace. Miko only writes to /workspace/."
    try:
        import aiofiles, os
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else WORKSPACE_DIR, exist_ok=True)
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)
        lines = content.count("\n") + 1
        return f"Written: {path} ({lines} lines, {len(content)} bytes)"
    except Exception as e:
        return f"write_file error: {e}"


async def _tool_git_status(repo: str = "miko-infra") -> str:
    """Run git status + diff --stat in a repo."""
    repo_path = REPO_PATHS.get(repo, REPO_PATHS["miko-infra"])
    try:
        import subprocess
        result = []
        for cmd in [
            ["git", "-C", repo_path, "status", "--short"],
            ["git", "-C", repo_path, "log", "--oneline", "-5"],
            ["git", "-C", repo_path, "diff", "--stat"],
        ]:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if r.stdout.strip():
                result.append("$ " + " ".join(cmd[2:]) + "\n" + r.stdout.strip())
        return "\n\n".join(result) if result else f"Repo {repo} is clean."
    except Exception as e:
        return f"git_status error: {e}"


async def _tool_git_commit(repo: str, message: str) -> str:
    """Stage all and commit. RED tier — only runs after explicit approval."""
    repo_path = REPO_PATHS.get(repo, REPO_PATHS["miko-infra"])
    try:
        import subprocess
        # Show diff first
        diff = subprocess.run(
            ["git", "-C", repo_path, "diff", "--stat", "HEAD"],
            capture_output=True, text=True, timeout=10
        )
        stage = subprocess.run(
            ["git", "-C", repo_path, "add", "-A"],
            capture_output=True, text=True, timeout=10
        )
        commit = subprocess.run(
            ["git", "-C", repo_path, "commit", "-m", message],
            capture_output=True, text=True, timeout=15
        )
        if commit.returncode == 0:
            return f"Committed in {repo}:\n{commit.stdout.strip()}\n\nDiff summary:\n{diff.stdout.strip() or 'no unstaged changes'}"
        else:
            return f"Commit failed:\n{commit.stderr.strip()}"
    except Exception as e:
        return f"git_commit error: {e}"


async def _resume_after_approval(
    messages: list[dict],
    user_id: str,
    user_message: str,
) -> str:
    """
    Re-enter the LLM after an approval callback to synthesize a final response.
    Strips tools array entirely and adds explicit synthesis instruction to force
    a plain text response — local model ignores tool_choice="none".
    """
    try:
        # Build synthesis-only messages — no tools, explicit instruction
        synth_messages = [m for m in messages if m.get("role") != "system"]

        # Prepend a minimal system prompt focused only on synthesis
        synth_system = (
            f"{SOUL}\n\n"
            "IMPORTANT: You have just received tool results. "
            "Do NOT call any more tools. Do NOT output <tool_call> or function call syntax. "
            "Synthesize the tool results into a clear, direct response to the user. "
            "Plain text only."
        )
        final_messages = [{"role": "system", "content": synth_system}] + synth_messages

        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{LLAMA_SERVER_URL}/v1/chat/completions",
                json={
                    "model": MODEL,
                    "messages": final_messages,
                    # No tools array — removes possibility of tool calls entirely
                    "stream": False,
                    "temperature": 0.7,
                    "max_tokens": 1024,
                },
            )
            resp.raise_for_status()
            reply = resp.json()["choices"][0]["message"].get("content", "").strip()

            # Strip any XML tool call syntax the model generated anyway
            reply = re.sub(r"<tool_call>.*?</tool_call>", "", reply, flags=re.DOTALL)
            reply = re.sub(r"<function=.*?</function>", "", reply, flags=re.DOTALL)
            reply = re.sub(r"<parameter=.*?</parameter>", "", reply, flags=re.DOTALL)
            reply = reply.strip()

            if not reply:
                return ""

            # Save to session history
            append_to_history(user_id, "user", user_message)
            append_to_history(user_id, "assistant", reply)
            asyncio.create_task(save_memory(
                user_id=user_id,
                messages=[
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": reply},
                ],
            ))
            return reply
    except Exception as e:
        logger.error("_resume_after_approval failed: %s", e)
        return ""


async def _dispatch_tool(tool_name: str, tool_args: dict, user_id: str) -> str:
    """Execute a tool directly, no permission check."""
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
    elif tool_name == "web_fetch":
        return await _tool_web_fetch(**tool_args)
    elif tool_name == "web_search":
        return await _tool_web_search(**tool_args)
    elif tool_name == "claude_query":
        return await _tool_claude_query(**tool_args)
    elif tool_name == "research":
        return await _tool_research(user_id=user_id, **tool_args)
    elif tool_name == "read_file":
        return await _tool_read_file(**tool_args)
    elif tool_name == "write_file":
        return await _tool_write_file(**tool_args)
    elif tool_name == "git_status":
        return await _tool_git_status(**tool_args)
    elif tool_name == "git_commit":
        return await _tool_git_commit(**tool_args)
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
        qdrant_url = os.getenv("QDRANT_URL", "http://awaas-qdrant:6333").rstrip("/")
        api_key = os.getenv("QDRANT_API_KEY", "")
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

# ---------------------------------------------------------------------------
# L3 — Reflection & Self-Correction
# ---------------------------------------------------------------------------

async def _reflect_on_tool_result(
    goal: str,
    tool_name: str,
    tool_result: str,
    iteration: int,
) -> dict:
    """
    After a tool executes, ask the fast model: is this result sufficient?
    Returns {"sufficient": bool, "gap": str, "suggestion": str}
    """
    # Skip reflection for cheap/fast tools — not worth the latency
    if tool_name in ("task_list", "infra_query", "remember", "task_add", "task_update"):
        return {"sufficient": True, "gap": "", "suggestion": ""}
    # Don't reflect on pending approvals or iteration 0 of simple ops
    if "⏳" in tool_result or iteration == 0 and tool_name not in ("web_search", "web_fetch", "research"):
        return {"sufficient": True, "gap": "", "suggestion": ""}

    prompt = f"""You are a critic evaluating whether a tool result adequately addresses the user's goal.

User goal: {goal[:300]}
Tool used: {tool_name}
Tool result (truncated): {tool_result[:800]}
Iteration: {iteration}

Answer in JSON only, no other text:
{{
  "sufficient": true/false,
  "gap": "what is still missing or unclear (empty string if sufficient)",
  "suggestion": "what tool or approach to try next (empty string if sufficient)"
}}"""

    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                f"{LLAMA_SERVER_URL}/v1/chat/completions",
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 150,
                    "temperature": 0.1,
                }
            )
            raw = r.json()["choices"][0]["message"]["content"].strip()
            # Strip markdown fences if present
            raw = re.sub(r"```json|```", "", raw).strip()
            return json.loads(raw)
    except Exception as e:
        logger.debug("Reflection failed (non-critical): %s", e)
        return {"sufficient": True, "gap": "", "suggestion": ""}


async def _critic_pass(
    user_message: str,
    response: str,
    tool_results: list[str],
) -> dict:
    """
    Final quality check on Miko's response before sending to user.
    Returns {"pass": bool, "issue": str, "rewrite_instruction": str}
    Only triggers for substantive responses (>100 chars) to avoid overhead on short replies.
    """
    if len(response) < 100 or not tool_results:
        return {"pass": True, "issue": "", "rewrite_instruction": ""}

    tools_summary = "\n".join(f"- {r[:200]}" for r in tool_results[-3:])
    prompt = f"""You are a quality critic for an AI assistant named Miko.

User asked: {user_message[:300]}
Miko responded: {response[:600]}
Tools used produced: {tools_summary}

Is Miko's response accurate, complete, and directly useful? Check for:
- Hallucinated facts not in tool results
- Missing critical information that was in the tool results
- Vague non-answers when concrete data was available

Answer in JSON only:
{{
  "pass": true/false,
  "issue": "specific problem if failed (empty if pass)",
  "rewrite_instruction": "what to fix if failed (empty if pass)"
}}"""

    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post(
                f"{LLAMA_SERVER_URL}/v1/chat/completions",
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 150,
                    "temperature": 0.1,
                }
            )
            raw = r.json()["choices"][0]["message"]["content"].strip()
            raw = re.sub(r"```json|```", "", raw).strip()
            return json.loads(raw)
    except Exception as e:
        logger.debug("Critic pass failed (non-critical): %s", e)
        return {"pass": True, "issue": "", "rewrite_instruction": ""}


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
    3. Call 35B with tools array (native tool_calls format)
    4. If model requests a tool call: execute it, reflect on result (L3), inject, re-call
    5. After final response: critic pass validates quality (L3), regenerates if needed
    6. Save exchange to session history + Mem0
    7. Max 5 tool call iterations per message to prevent runaway loops
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
    # Keep typing indicator alive during inference
    _typing_chat_id = ERIC_CHAT_ID if user_id == "eric" else DAVID_CHAT_ID
    asyncio.create_task(_send_typing(_typing_chat_id, duration=60))

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

                    # If tool is pending approval, save context and return holding message
                    if tool_result.startswith("⏳ Waiting for approval"):
                        # Extract approval ID from result e.g. "⏳ Waiting for approval `[abc123]`"
                        approval_id_match = re.search(r"\[([a-f0-9]+)\]", tool_result)
                        if approval_id_match:
                            approval_id = approval_id_match.group(1)
                            # Save full message context so we can resume after approval
                            _pending_contexts[approval_id] = {
                                "messages": messages + [msg, {
                                    "role": "tool",
                                    "tool_call_id": tool_call.get("id", "call_0"),
                                    "content": "__PENDING__",  # placeholder, filled on approval
                                }],
                                "user_id": user_id,
                                "user_message": user_message,
                                "tool_name": tool_name,
                            }
                            logger.info("Saved context for approval %s", approval_id)
                        final_reply = tool_result
                        break

                    # ── L3: Reflect on tool result — is it sufficient? ─────
                    reflection = await _reflect_on_tool_result(
                        goal=user_message,
                        tool_name=tool_name,
                        tool_result=tool_result,
                        iteration=tool_iterations,
                    )
                    if not reflection.get("sufficient") and reflection.get("suggestion") and tool_iterations < MAX_TOOL_ITERATIONS - 1:
                        logger.info("L3 reflection: gap=%s suggestion=%s", reflection.get("gap","")[:80], reflection.get("suggestion","")[:80])
                        # Inject reflection hint into tool result so model knows to go deeper
                        tool_result = tool_result + f"\n\n[REFLECTION: The above may be incomplete. Gap: {reflection['gap']}. Consider: {reflection['suggestion']}]"

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
                        # L3: inject parse failure so model can self-correct
                        messages.append({"role": "assistant", "content": content})
                        messages.append({"role": "user", "content": f"[SYSTEM: Tool call parse failed: {e}. Please reformat your tool call correctly and try again.]"})
                        tool_iterations += 1
                        continue

                # ── Path C: no tool call — final response ──────────────────
                final_reply = content.strip()

                # ── L3: Critic pass — verify response quality ─────────────
                if tool_iterations > 0:  # only run critic if tools were used
                    tool_results_log = [
                        m.get("content", "") for m in messages
                        if m.get("role") == "tool"
                    ]
                    critic = await _critic_pass(user_message, final_reply, tool_results_log)
                    if not critic.get("pass") and critic.get("rewrite_instruction") and tool_iterations < MAX_TOOL_ITERATIONS:
                        logger.info("L3 critic failed — rewriting. Issue: %s", critic.get("issue","")[:100])
                        # Inject critic feedback and regenerate once
                        messages.append({"role": "assistant", "content": final_reply})
                        messages.append({
                            "role": "user",
                            "content": f"[SELF-CORRECTION]: Your response has an issue: {critic['issue']}. Fix: {critic['rewrite_instruction']}. Rewrite your response addressing this."
                        })
                        tool_iterations += 1
                        continue  # loop back for one correction pass
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
