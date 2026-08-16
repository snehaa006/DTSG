"""Tests for the event-log write path.

The database's append-only guarantee is enforced by triggers (migration 0002)
and was verified directly against Supabase; there is no local Postgres here, so
these tests use a recording fake connection. That means they prove the SQL we
*send* and the route behaviour around it — the parameter order, the keyset
cursor, the absence of any mutation path — rather than re-proving Postgres.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from app.deps import get_conn, get_embeddings, get_llm
from app.main import app
from app.schemas import Event
from app.store import events as events_store

USER = UUID("00000000-0000-0000-0000-000000000001")


def _row(raw_text: str = "hello", ts: datetime | None = None) -> dict:
    return {
        "id": uuid4(),
        "user_id": USER,
        "raw_text": raw_text,
        "timestamp": ts or datetime.now(timezone.utc),
    }


class FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    """Records SQL and args; replays queued results."""

    def transaction(self):
        return FakeTx()

    def __init__(self, fetchrow=None, fetch=None, fetchval=None) -> None:
        self._fetchrow = fetchrow
        self._fetch = fetch or []
        self._fetchval = fetchval
        self.calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        return self._fetchrow

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self._fetch

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return self._fetchval

    @property
    def last_sql(self) -> str:
        return " ".join(self.calls[-1][0].split())

    @property
    def last_args(self) -> tuple:
        return self.calls[-1][1]


def _client(conn: FakeConn) -> httpx.AsyncClient:
    async def override():
        yield conn

    app.dependency_overrides[get_conn] = override
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


# --- store -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_lets_the_database_set_the_timestamp() -> None:
    """Ordering must come from one clock, not from whichever instance served."""
    conn = FakeConn(fetchrow=_row())
    await events_store.insert_event(conn, user_id=USER, raw_text="hi")

    assert "insert into events (user_id, raw_text)" in conn.last_sql
    assert "timestamp" not in conn.last_sql.split("values")[0]
    assert conn.last_args == (USER, "hi")


@pytest.mark.asyncio
async def test_insert_returns_the_persisted_row() -> None:
    row = _row("recorded")
    conn = FakeConn(fetchrow=row)
    event = await events_store.insert_event(conn, user_id=USER, raw_text="recorded")

    assert isinstance(event, Event)
    assert event.id == row["id"]
    assert event.raw_text == "recorded"


@pytest.mark.asyncio
async def test_list_orders_newest_first_by_timestamp_and_id() -> None:
    conn = FakeConn(fetch=[_row()])
    await events_store.list_events(conn, user_id=USER, limit=10)

    assert "order by timestamp desc, id desc" in conn.last_sql
    assert conn.last_args == (USER, 10)


@pytest.mark.asyncio
async def test_cursor_compares_the_timestamp_and_id_pair() -> None:
    """Comparing timestamp alone drops or repeats same-millisecond messages."""
    ts = datetime.now(timezone.utc)
    cursor_id = uuid4()
    conn = FakeConn(fetch=[])
    await events_store.list_events(
        conn, user_id=USER, limit=5, before_timestamp=ts, before_id=cursor_id
    )

    assert "(timestamp, id) < ($2, $3)" in conn.last_sql
    assert conn.last_args == (USER, ts, cursor_id, 5)


def test_store_exposes_no_mutation_helpers() -> None:
    """The DB rejects UPDATE/DELETE; don't imply otherwise in Python."""
    names = [n for n in dir(events_store) if not n.startswith("_")]
    assert not [n for n in names if re.search(r"update|delete|edit|modify", n)]


# --- routes ----------------------------------------------------------------


class NoFactsLLM:
    """Extracts nothing, so chat exercises the event write and stops there."""

    async def complete(self, **kwargs):  # pragma: no cover
        raise NotImplementedError

    async def complete_structured(self, *, schema, **kwargs):
        return schema(facts=[])


class NoopEmbeddings:
    async def embed(self, texts):  # pragma: no cover - unreached with no facts
        return [[0.0]]


def _chat_client(conn: FakeConn) -> httpx.AsyncClient:
    async def conn_override():
        yield conn

    app.dependency_overrides[get_conn] = conn_override
    app.dependency_overrides[get_llm] = lambda: NoFactsLLM()
    app.dependency_overrides[get_embeddings] = lambda: NoopEmbeddings()
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://t")


@pytest.mark.asyncio
async def test_chat_appends_an_event_and_returns_its_id() -> None:
    row = _row("I moved to Berlin")
    conn = FakeConn(fetchrow=row)
    try:
        async with _chat_client(conn) as client:
            response = await client.post(
                "/api/chat", json={"user_id": str(USER), "message": "I moved to Berlin"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["event_id"] == str(row["id"])
    assert conn.calls[0][1] == (USER, "I moved to Berlin")


@pytest.mark.asyncio
async def test_invalid_chat_payload_never_reaches_the_database() -> None:
    conn = FakeConn(fetchrow=_row())
    try:
        async with _chat_client(conn) as client:
            response = await client.post(
                "/api/chat", json={"user_id": "not-a-uuid", "message": "x"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert conn.calls == []


@pytest.mark.asyncio
async def test_full_page_returns_a_cursor() -> None:
    ts = datetime.now(timezone.utc)
    rows = [_row(ts=ts - timedelta(seconds=i)) for i in range(3)]
    conn = FakeConn(fetch=rows)
    try:
        async with _client(conn) as client:
            response = await client.get(f"/api/events?user_id={USER}&limit=3")
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    assert len(body["events"]) == 3
    assert body["next_before_id"] == str(rows[-1]["id"])


@pytest.mark.asyncio
async def test_short_page_ends_pagination() -> None:
    conn = FakeConn(fetch=[_row()])
    try:
        async with _client(conn) as client:
            response = await client.get(f"/api/events?user_id={USER}&limit=50")
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert body["next_before_id"] is None
    assert body["next_before_timestamp"] is None


@pytest.mark.asyncio
async def test_half_a_cursor_is_rejected() -> None:
    """Silently paging from the head would look like duplicated results."""
    conn = FakeConn(fetch=[])
    try:
        async with _client(conn) as client:
            response = await client.get(
                f"/api/events?user_id={USER}&before_timestamp=2026-01-01T00:00:00Z"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert conn.calls == []


@pytest.mark.asyncio
async def test_missing_event_is_404_not_500() -> None:
    conn = FakeConn(fetchrow=None)
    try:
        async with _client(conn) as client:
            response = await client.get(f"/api/events/{uuid4()}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_event_log_exposes_no_write_verbs() -> None:
    paths = {
        (r.path, m)
        for r in app.routes
        if hasattr(r, "methods")
        for m in r.methods
        if r.path.startswith("/api/events")
    }
    assert not {p for p in paths if p[1] in {"PATCH", "PUT", "DELETE", "POST"}}
