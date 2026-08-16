"""Persistence for the immutable event log.

There is deliberately no `update_event` or `delete_event` here. The database
rejects both (migration 0002), and offering a Python function that could only
ever raise would suggest the operation is merely discouraged rather than
impossible.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from ..schemas import Event

_COLUMNS = "id, user_id, raw_text, timestamp"


def _to_event(row: asyncpg.Record) -> Event:
    return Event(
        id=row["id"],
        user_id=row["user_id"],
        raw_text=row["raw_text"],
        timestamp=row["timestamp"],
    )


async def insert_event(
    conn: asyncpg.Connection, *, user_id: UUID, raw_text: str
) -> Event:
    """Append one message to the log.

    `timestamp` is left to the column default so it comes from the database
    clock. Taking it from the API process would make ordering depend on which
    Render instance handled the request, and the retrieval decay in Phase 5
    reads these timestamps directly.
    """
    row = await conn.fetchrow(
        f"insert into events (user_id, raw_text) values ($1, $2) returning {_COLUMNS}",
        user_id,
        raw_text,
    )
    return _to_event(row)


async def get_event(conn: asyncpg.Connection, event_id: UUID) -> Event | None:
    row = await conn.fetchrow(f"select {_COLUMNS} from events where id = $1", event_id)
    return _to_event(row) if row is not None else None


async def list_events(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    limit: int = 50,
    before_timestamp: datetime | None = None,
    before_id: UUID | None = None,
) -> list[Event]:
    """Return a user's events, newest first.

    Keyset pagination on `(timestamp, id)` rather than OFFSET: the log only ever
    grows at the head, so OFFSET would shift rows under a paging client. The id
    is part of the key because two messages sent in the same millisecond share a
    timestamp, and ordering by timestamp alone would drop or repeat one of them.
    """
    if before_timestamp is not None and before_id is not None:
        rows = await conn.fetch(
            f"""
            select {_COLUMNS} from events
            where user_id = $1 and (timestamp, id) < ($2, $3)
            order by timestamp desc, id desc
            limit $4
            """,
            user_id,
            before_timestamp,
            before_id,
            limit,
        )
    else:
        rows = await conn.fetch(
            f"""
            select {_COLUMNS} from events
            where user_id = $1
            order by timestamp desc, id desc
            limit $2
            """,
            user_id,
            limit,
        )
    return [_to_event(row) for row in rows]


async def count_events(conn: asyncpg.Connection, user_id: UUID) -> int:
    return await conn.fetchval(
        "select count(*) from events where user_id = $1", user_id
    )
