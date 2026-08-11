"""Shared asyncpg connection pool for bot writes and dashboard reads.

Reads DATABASE_URL from env once, lazily creates a single Pool per process.
Callers should always use `get_pool()` — never construct their own pool.
"""
from __future__ import annotations

import os
from typing import Optional

import asyncpg


_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError(
                "DATABASE_URL is not set. Add your Supabase connection string "
                "to .env — see README/setup instructions."
            )
        # min_size=1 keeps a warm connection so first turn doesn't pay
        # connect latency. max_size=5 is plenty for a single bot process.
        _pool = await asyncpg.create_pool(url, min_size=1, max_size=5)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
