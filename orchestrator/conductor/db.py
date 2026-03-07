"""
Lightweight asyncpg wrapper for conductor state persistence.
Stores chat_ids and conductor state in master-postgres.
"""
from __future__ import annotations

import logging

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
    pool = _pool
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS conductor_state (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS conductor_chat_ids (
                chat_id       BIGINT PRIMARY KEY,
                username      TEXT,
                registered_at TIMESTAMPTZ DEFAULT NOW(),
                active        BOOLEAN DEFAULT TRUE
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id              SERIAL PRIMARY KEY,
                name            TEXT NOT NULL UNIQUE,
                data_class      TEXT NOT NULL,
                priority        INT NOT NULL,
                daily_spend_cap NUMERIC(10,2) NOT NULL
            )
        """)
        await conn.execute("""
            INSERT INTO projects (name, data_class, priority, daily_spend_cap) VALUES
                ('pleadly', 'PRIVILEGED', 1, 30.00),
                ('awaas',   'INTERNAL',   2, 50.00),
                ('trading', 'INTERNAL',   3, 10.00)
            ON CONFLICT (name) DO NOTHING
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS health_log (
                ts         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                project_id INT REFERENCES projects(id),
                service    TEXT NOT NULL,
                status     TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS spend_log (
                ts         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                project_id INT REFERENCES projects(id),
                provider   TEXT NOT NULL,
                cost_usd   NUMERIC(10,4) NOT NULL
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


async def log_health(service: str, project_name: str, status: str) -> None:
    """Persist a health check result to health_log."""
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO health_log (service, project_id, status)
                SELECT $1, id, $3 FROM projects WHERE name = $2
            """, service, project_name, status)
    except Exception as exc:
        logger.error("Failed to log health: %s", exc)


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
