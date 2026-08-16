"""Chat entry point.

Phase 3 makes this write: every incoming message is appended to the event log
before anything else happens. That ordering is deliberate — the raw message is
the ground truth the rest of the system derives from, so it is recorded first
and unconditionally, even if later stages (extraction in Phase 4, retrieval in
Phase 5) fail or are not built yet.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends

from ..deps import get_conn
from ..schemas import ChatRequest, ChatResponse
from ..store import events as events_store

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    conn: asyncpg.Connection = Depends(get_conn),
) -> ChatResponse:
    event = await events_store.insert_event(
        conn, user_id=payload.user_id, raw_text=payload.message
    )
    return ChatResponse(
        reply=(
            "Recorded to the event log. Fact extraction and the memory write "
            "pipeline land in Phase 4."
        ),
        event_id=event.id,
    )
