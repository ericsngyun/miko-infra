"""
Lightweight asyncpg wrapper for conductor state persistence.
Stores chat_ids and conductor state in master-postgres.
"""
from __future__ import annotations

import logging
from typing import Any

import asyncpg

from settings import settings

logger = logging.getLogger("conductor.db")

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=settings.pg_host,
            port=settings.pg_port,
            user=settings.pg_user,
            password=settings.pg_password,
            database=settings.pg_database,
            min_size=1,
            max_size=5,
        )
        await _ensure_schema()
        logger.info("Database pool initialized")
    return _pool


async def _ensure_schema() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS conductor_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS conductor_chat_ids (
                chat_id    BIGINT PRIMARY KEY,
                username   TEXT,
                registered_at TIMESTAMPTZ DEFAULT NOW(),
                active     BOOLEAN DEFAULT TRUE
            )
        """)
    logger.info("Schema verified")


async def get_chat_ids() -> list[int]:
    """Load all active chat_ids from postgres."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT chat_id FROM conductor_chat_ids WHERE active = TRUE"
            )
            return [row["chat_id"] for row in rows]
    except Exception as exc:
        logger.error("Failed to load chat_ids: %s", exc)
        return []


async def save_chat_id(chat_id: int, username: str | None = None) -> None:
    """Upsert a chat_id into postgres."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO conductor_chat_ids (chat_id, username, active)
                VALUES ($1, $2, TRUE)
                ON CONFLICT (chat_id) DO UPDATE
                SET username = $2, active = TRUE, registered_at = NOW()
            """, chat_id, username)
        logger.info("Saved chat_id=%s username=%s", chat_id, username)
    except Exception as exc:
        logger.error("Failed to save chat_id: %s", exc)


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
