"""
miko-api — Miko Labs AI assistant.
Direct qdrant memory, single inference call per request.
"""
from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from memory import search_memories, store_memory
from settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("miko.api")

app = FastAPI(title="miko-api", version="0.2.0")
_executor = ThreadPoolExecutor(max_workers=4)

SYSTEM_PROMPT = """You are Miko, the AI operations core of Miko Labs.
You are precise, direct, and mission-focused. You help Eric and David run their agentic workforce business.
You have memory of past conversations and use it to give contextually relevant responses.
You never give generic advice. Every response is specific to Miko Labs current state and goals.
Keep responses concise unless depth is explicitly requested.
If you don't have specific information in memory, say so directly in one sentence and ask for clarification. Never speculate or pad responses."""


class ChatRequest(BaseModel):
    message: str
    user_id: str = "eric"


class ChatResponse(BaseModel):
    response: str
    memories_used: int = 0


def _auth(x_api_key: str | None) -> None:
    if x_api_key != settings.miko_api_key:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "online", "model": settings.chat_model}


@app.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    x_api_key: str | None = Header(default=None),
) -> ChatResponse:
    _auth(x_api_key)

    loop = asyncio.get_event_loop()

    # Retrieve relevant memories (embedding search only — no LLM)
    memories = await loop.run_in_executor(
        _executor,
        lambda: search_memories(req.message, req.user_id, settings.ollama_url, limit=5)
    )

    # Build context
    memory_context = ""
    if memories:
        lines = [f"- {m['text']}" for m in memories if m.get("score", 0) > 0.5]
        if lines:
            memory_context = "\nRelevant past context:\n" + "\n".join(lines[:3])

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + memory_context},
        {"role": "user", "content": req.message},
    ]

    # Single inference call
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{settings.ollama_url}/api/chat",
                json={
                    "model": settings.chat_model,
                    "messages": messages,
                    "stream": False,
                    "think": False,
                    "options": {
                        "num_predict": 512,
                        "temperature": 0.7,
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            raw = data["message"]["content"]
            logger.info("RAW RESPONSE (first 200): %s", raw[:200])
            # Strip think tags if present
            response_text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            if not response_text:
                response_text = raw.strip()
    except Exception as exc:
        logger.error("Ollama request failed: %s", exc)
        raise HTTPException(status_code=503, detail="Inference unavailable")

    # Store memory async — never blocks response
    _executor.submit(store_memory, req.user_id, req.message, response_text, settings.ollama_url)

    return ChatResponse(response=response_text, memories_used=len(memories))
