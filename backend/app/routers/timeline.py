"""Phase 6 — supersede chains for the timeline view."""

from __future__ import annotations

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query

from ..deps import get_conn
from ..schemas import TimelineResponse
from ..store import memories as memories_store
from ..timeline import build_chains

router = APIRouter(prefix="/api", tags=["timeline"])


@router.get("/timeline", response_model=TimelineResponse)
async def timeline(
    user_id: UUID,
    limit: int = Query(default=200, ge=1, le=1000),
    conn: asyncpg.Connection = Depends(get_conn),
) -> TimelineResponse:
    """Every memory, arranged into supersede chains, newest activity first.

    Fetches both statuses deliberately: a chain with its expired entries removed
    is just a list of current facts, which is what an ordinary memory store
    would show. The superseded rows are the history.
    """
    rows = await memories_store.list_memories(conn, user_id=user_id, limit=limit)
    return TimelineResponse(chains=build_chains(rows))
