"""Postgres access via asyncpg.

We talk to Supabase through the transaction pooler (pgbouncer), which does not
support prepared statements. That forces `statement_cache_size=0`; without it
asyncpg raises `DuplicatePreparedStatementError` on the second request that
reuses a pooled connection.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

import asyncpg

from .config import Settings

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def _normalize_dsn(dsn: str) -> str:
    """Strip query params asyncpg does not understand (e.g. `?pgbouncer=true`).

    Supabase's dashboard hands out URIs with libpq/pgbouncer-flavoured query
    strings. asyncpg rejects unknown ones outright, so drop the query entirely;
    the settings we care about are passed as explicit connect args instead.
    """
    parts = urlsplit(dsn)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


async def connect(settings: Settings) -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    _pool = await asyncpg.create_pool(
        _normalize_dsn(settings.database_url),
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        statement_cache_size=0,
        command_timeout=30,
    )
    log.info("db pool ready (min=%s max=%s)", settings.db_pool_min, settings.db_pool_max)
    return _pool


async def disconnect() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("db pool not initialized; did the lifespan hook run?")
    return _pool


@asynccontextmanager
async def acquire():
    async with get_pool().acquire() as conn:
        yield conn


async def ping() -> bool:
    """Cheap liveness probe used by /health."""
    async with acquire() as conn:
        return await conn.fetchval("select 1") == 1
