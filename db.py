"""Shared asyncpg connection pool for bot writes and dashboard reads.

asyncpg pools are bound to the event loop that created them. Since the
dashboard API runs in a daemon thread with its own loop while the bot
pipeline runs on the main loop, we keep one pool per loop.
"""
from __future__ import annotations

import asyncio
import os
from typing import Dict

import asyncpg


_pools: Dict[int, asyncpg.Pool] = {}


async def get_pool() -> asyncpg.Pool:
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    pool = _pools.get(loop_id)
    if pool is not None:
        return pool

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add your Supabase connection string "
            "to .env — see README/setup instructions."
        )
    # min_size=1 keeps a warm connection so first turn doesn't pay connect
    # latency. max_size=20 supports concurrent sessions on this loop; Supabase
    # transaction pooler handles ~200 total across all clients.
    pool = await asyncpg.create_pool(url, min_size=1, max_size=20)
    _pools[loop_id] = pool
    return pool


async def close_pool() -> None:
    """Close the pool tied to the current event loop, if any."""
    loop_id = id(asyncio.get_running_loop())
    pool = _pools.pop(loop_id, None)
    if pool is not None:
        await pool.close()
