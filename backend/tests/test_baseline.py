"""Tests for the naive-RAG baseline and the comparison endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from app.deps import get_conn, get_embeddings
from app.main import app
from app.schemas import Memory

USER = UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime.now(timezone.utc)


def _mem(obj, *, status="ACTIVE", days_ago=10, superseded_by=None) -> Memory:
    return Memory(
        id=uuid4(),
        user_id=USER,
        subject="user",
        predicate="lives_in",
        object=obj,
        status=status,
        valid_from=NOW - timedelta(days=days_ago),
        valid_until=None if status == "ACTIVE" else NOW - timedelta(days=10),
        superseded_by=superseded_by,
    )


BERLIN = _mem("Berlin", days_ago=10)
DELHI = _mem("Delhi", status="EXPIRED", days_ago=400, superseded_by=BERLIN.id)


def _row(memory: Memory, similarity: float) -> dict:
    data = memory.model_dump()
    data["similarity"] = similarity
    return data


class StubEmbeddings:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return [[1.0] + [0.0] * 1535]


class FakeConn:
    """Returns the same rows for every query; both retrievals see one corpus."""

    def __init__(self, rows) -> None:
        self.rows = rows
        self.queries: list[str] = []

    async def fetch(self, sql, *args):
        self.queries.append(" ".join(sql.split()))
        return self.rows


def _client(conn, embeddings) -> httpx.AsyncClient:
    async def conn_override():
        yield conn

    app.dependency_overrides[get_conn] = conn_override
    app.dependency_overrides[get_embeddings] = lambda: embeddings
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


# The stale fact is the *better* textual match, which is precisely the case
# naive retrieval gets wrong.
ROWS = [_row(DELHI, 0.92), _row(BERLIN, 0.88)]


@pytest.mark.asyncio
async def test_baseline_ranks_by_similarity_alone() -> None:
    conn = FakeConn(ROWS)
    try:
        async with _client(conn, StubEmbeddings()) as client:
            response = await client.post(
                "/api/baseline/retrieve",
                json={"user_id": str(USER), "query": "where do I live?"},
            )
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    # Superseded fact wins on similarity — the baseline has no way to know.
    assert body["results"][0]["object"] == "Delhi"
    assert body["results"][0]["status"] == "EXPIRED"


@pytest.mark.asyncio
async def test_baseline_query_applies_no_status_or_time_filter() -> None:
    conn = FakeConn(ROWS)
    try:
        async with _client(conn, StubEmbeddings()) as client:
            await client.post(
                "/api/baseline/retrieve",
                json={"user_id": str(USER), "query": "x"},
            )
    finally:
        app.dependency_overrides.clear()

    # These columns are selected (the caller needs them to report staleness);
    # what matters is that none of them narrows or orders the result.
    where_clause = conn.queries[0].split(" where ")[1]
    assert "status" not in where_clause
    assert "valid_until" not in where_clause
    assert "valid_from" not in where_clause
    # Ordering is by vector distance and nothing else.
    assert where_clause.split(" order by ")[1].startswith("embedding <=>")


@pytest.mark.asyncio
async def test_compare_shows_dtsg_correcting_the_baseline() -> None:
    """The headline result: same corpus, same query, different answer."""
    conn = FakeConn(ROWS)
    try:
        async with _client(conn, StubEmbeddings()) as client:
            response = await client.post(
                "/api/baseline/compare",
                json={"user_id": str(USER), "query": "where do I live?", "limit": 5},
            )
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert body["baseline"][0]["object"] == "Delhi"
    assert body["dtsg"][0]["object"] == "Berlin"
    assert body["baseline_top_is_stale"] is True
    assert body["dtsg_top_is_stale"] is False


@pytest.mark.asyncio
async def test_compare_embeds_the_query_once_for_both_sides() -> None:
    """Two embeddings of the same text could differ; that would confound it."""
    embeddings = StubEmbeddings()
    conn = FakeConn(ROWS)
    try:
        async with _client(conn, embeddings) as client:
            await client.post(
                "/api/baseline/compare",
                json={"user_id": str(USER), "query": "where do I live?"},
            )
    finally:
        app.dependency_overrides.clear()

    assert embeddings.calls == 1


@pytest.mark.asyncio
async def test_overlap_is_one_when_both_return_the_same_rows() -> None:
    conn = FakeConn(ROWS)
    try:
        async with _client(conn, StubEmbeddings()) as client:
            response = await client.post(
                "/api/baseline/compare",
                json={"user_id": str(USER), "query": "x", "limit": 5},
            )
    finally:
        app.dependency_overrides.clear()

    # Same rows, different order: the divergence is ranking, not recall.
    assert response.json()["overlap"] == 1.0


@pytest.mark.asyncio
async def test_empty_corpus_compares_cleanly() -> None:
    conn = FakeConn([])
    try:
        async with _client(conn, StubEmbeddings()) as client:
            response = await client.post(
                "/api/baseline/compare", json={"user_id": str(USER), "query": "x"}
            )
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert body["overlap"] == 1.0
    assert body["baseline_top_is_stale"] is False
    assert body["dtsg_top_is_stale"] is False


@pytest.mark.asyncio
async def test_embedding_failure_is_502() -> None:
    class Broken:
        async def embed(self, texts):
            raise RuntimeError("down")

    conn = FakeConn([])
    try:
        async with _client(conn, Broken()) as client:
            response = await client.post(
                "/api/baseline/retrieve", json={"user_id": str(USER), "query": "x"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
