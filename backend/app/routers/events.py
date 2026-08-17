"""Read access to the immutable event log.

Only reads are exposed. There is no PATCH or DELETE here, and adding one would
not work anyway — the database rejects both.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import get_conn
from ..schemas import Event, EventListResponse
from ..store import events as events_store

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events", response_model=EventListResponse)
async def list_events(
    user_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    before_timestamp: datetime | None = None,
    before_id: UUID | None = None,
    conn: asyncpg.Connection = Depends(get_conn),
) -> EventListResponse:
    # The cursor is a (timestamp, id) pair; half of one would silently page from
    # the head instead of where the caller left off, so require both or neither.
    if (before_timestamp is None) != (before_id is None):
        raise HTTPException(
            status_code=422,
            detail="before_timestamp and before_id must be provided together",
        )

    rows = await events_store.list_events(
        conn,
        user_id=user_id,
        limit=limit,
        before_timestamp=before_timestamp,
        before_id=before_id,
    )

    # A full page means there may be more; a short page is definitively the end.
    if len(rows) == limit:
        last = rows[-1]
        return EventListResponse(
            events=rows,
            next_before_timestamp=last.timestamp,
            next_before_id=last.id,
        )
    return EventListResponse(events=rows)


@router.get("/events/{event_id}", response_model=Event)
async def get_event(
    event_id: UUID,
    conn: asyncpg.Connection = Depends(get_conn),
) -> Event:
    event = await events_store.get_event(conn, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="event not found")
    return event
