"""Read access to the state graph.

Exposed now because it is the only way to confirm Phase 4 actually expired
something. Phase 6's timeline view builds on the same endpoints.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_conn
from ..schemas import Memory, MemoryListResponse
from ..store import memories as memories_store

router = APIRouter(prefix="/api", tags=["memories"])


@router.get("/memories", response_model=MemoryListResponse)
async def list_memories(
    user_id: UUID,
    status: Literal["ACTIVE", "EXPIRED"] | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    conn: asyncpg.Connection = Depends(get_conn),
) -> MemoryListResponse:
    """List memories, newest first. Omit `status` to see expired ones too."""
    rows = await memories_store.list_memories(
        conn, user_id=user_id, status=status, limit=limit
    )
    return MemoryListResponse(memories=rows)


@router.get("/memories/{memory_id}", response_model=Memory)
async def get_memory(
    memory_id: UUID,
    conn: asyncpg.Connection = Depends(get_conn),
) -> Memory:
    memory = await memories_store.get_memory(conn, memory_id)
    if memory is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return memory
