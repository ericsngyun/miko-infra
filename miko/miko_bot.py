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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from telegram import Update
from telegram.ext import (
    Application,
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
PLEADLY_API_URL  = os.getenv("PLEADLY_API_URL",  "http://172.23.0.1:8300")
QDRANT_URL       = os.getenv("QDRANT_URL",        "http://qdrant:6333")
QDRANT_API_KEY   = os.getenv("QDRANT_API_KEY",    None)
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "")
ERIC_CHAT_ID     = int(os.getenv("ERIC_CHAT_ID",   "7355900090"))
DAVID_CHAT_ID    = int(os.getenv("DAVID_CHAT_ID",  "1697120532"))
MODEL            = os.getenv("MIKO_MODEL",         "qwen3.5")

SOUL_MD_PATH = Path(__file__).parent / "SOUL.md"

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
        return [r["memory"] for r in results.get("results", [])]
    except Exception as e:
        logger.warning("Memory retrieval failed: %s", e)
        return []


async def save_memory(user_id: str, messages: list[dict]) -> None:
    """Extract and save facts from conversation turn via Mem0."""
    if not MEMORY_AVAILABLE or _memory is None:
        return
    try:
        await asyncio.get_event_loop().run_in_executor(None, lambda: _memory.add(messages=messages, user_id=user_id))
    except Exception as e:
        logger.warning("Memory save failed: %s", e)

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
    Core reasoning call. Assembles context from:
    1. SOUL.md system prompt
    2. Relevant Mem0 memories
    3. Optional live Pleadly status
    4. User message
    """
    # Build system prompt
    system_parts = [SOUL]

    # Inject current date/time
    now = datetime.now(timezone.utc).strftime("%A, %B %d, %Y at %H:%M UTC")
    system_parts.append(f"\n\n---\nCurrent date/time: {now}")

    # Inject relevant memories
    memories = await get_memories(user_id=user_id, query=user_message)
    if memories:
        memory_block = "\n".join(f"- {m}" for m in memories)
        system_parts.append(f"\n\n---\nWhat you remember about this person and context:\n{memory_block}")

    # Inject Pleadly status if requested or if message seems infra-related
    infra_keywords = ["pleadly", "status", "health", "pipeline", "api", "server", "running", "down"]
    if include_pleadly_status or any(k in user_message.lower() for k in infra_keywords):
        pleadly_status = await get_pleadly_status()
        status_str = format_pleadly_status(pleadly_status)
        system_parts.append(f"\n\n---\nLive system status (just fetched):\n{status_str}")

    system_prompt = "".join(system_parts)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{LLAMA_SERVER_URL}/v1/chat/completions",
                json={
                    "model": MODEL,
                    "messages": messages,
                    "stream": False,
                    "temperature": 0.7,
                    "max_tokens": 1024,
                },
            )
            response.raise_for_status()
            data = response.json()
            reply = data["choices"][0]["message"]["content"].strip()

        # Save to memory (fire and forget)
        asyncio.create_task(save_memory(
            user_id=user_id,
            messages=[
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": reply},
            ],
        ))

        return reply

    except Exception as e:
        logger.error("LLM call failed: %s", e)
        return "Something broke on my end. Check the logs — I'll be back in a second."

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

    reply = await chat_with_miko(user_message=user_message, user_id=user_id)

    # Telegram has 4096 char limit — split if needed
    if len(reply) <= 4096:
        await update.message.reply_text(reply)
    else:
        chunks = [reply[i:i+4096] for i in range(0, len(reply), 4096)]
        for chunk in chunks:
            await update.message.reply_text(chunk)


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — live Pleadly + system status."""
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    status = await get_pleadly_status()
    status_str = format_pleadly_status(status)

    chat_id = update.effective_chat.id
    user_id = get_user_id(chat_id)

    reply = await chat_with_miko(
        user_message=f"Give me a status summary. Here's the live data:\n{status_str}",
        user_id=user_id,
        include_pleadly_status=False,  # already injected above
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
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
