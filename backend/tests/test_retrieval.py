"""Tests for temporal scoring and the /api/retrieve route.

Scoring is a pure function of already-fetched rows, so the behaviour that
matters — a superseded fact losing to its replacement, a point-in-time query
treating an expired fact as current — is tested directly rather than through the
database.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from app.deps import get_conn, get_embeddings
from app.main import app
from app.retrieval import (
    DEFAULT_EXPIRED_WEIGHT,
    DEFAULT_LAMBDA_PER_DAY,
    candidate_pool_size,
    recency_factor,
    reference_time,
    rerank,
    score_memory,
    status_weight,
)
from app.schemas import Memory

USER = UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def _mem(
    obj="Berlin",
    *,
    status="ACTIVE",
    valid_from=None,
    valid_until=None,
    last_reinforced_at=None,
) -> Memory:
    return Memory(
        id=uuid4(),
        user_id=USER,
        subject="user",
        predicate="lives_in",
        object=obj,
        status=status,
        confidence=0.9,
        valid_from=valid_from or NOW - timedelta(days=30),
        valid_until=valid_until,
        last_reinforced_at=last_reinforced_at,
    )


# --- status weight ---------------------------------------------------------


def test_active_fact_gets_full_weight() -> None:
    assert status_weight(_mem(), at=NOW) == 1.0


def test_expired_fact_is_discounted_not_excluded() -> None:
    """Discounting is the whole point: the old fact stays reachable."""
    expired = _mem("Delhi", status="EXPIRED", valid_until=NOW - timedelta(days=1))
    weight = status_weight(expired, at=NOW)
    assert weight == DEFAULT_EXPIRED_WEIGHT
    assert 0 < weight < 1


def test_expired_fact_counts_as_current_for_a_time_before_it_expired() -> None:
    """A fact superseded last week was true a month ago."""
    expired = _mem(
        "Delhi",
        status="EXPIRED",
        valid_from=NOW - timedelta(days=365),
        valid_until=NOW - timedelta(days=7),
    )
    assert status_weight(expired, at=NOW - timedelta(days=30)) == 1.0
    assert status_weight(expired, at=NOW) == DEFAULT_EXPIRED_WEIGHT


def test_fact_recorded_after_the_asked_about_moment_is_discounted() -> None:
    """It had not happened yet; it should not answer as if it had."""
    future = _mem(valid_from=NOW - timedelta(days=1))
    assert status_weight(future, at=NOW - timedelta(days=10)) == DEFAULT_EXPIRED_WEIGHT


# --- recency ---------------------------------------------------------------


def test_recency_decays_with_age() -> None:
    fresh = _mem(valid_from=NOW)
    old = _mem(valid_from=NOW - timedelta(days=365))
    assert recency_factor(fresh, at=NOW) > recency_factor(old, at=NOW)


def test_default_lambda_halves_over_its_half_life() -> None:
    aged = _mem(valid_from=NOW - timedelta(days=180))
    assert recency_factor(aged, at=NOW) == pytest.approx(0.5, abs=1e-3)


def test_reinforcement_makes_an_old_fact_rank_as_fresh() -> None:
    """Otherwise REINFORCE has no effect on retrieval at all."""
    stated_long_ago = _mem(valid_from=NOW - timedelta(days=365))
    restated = _mem(
        valid_from=NOW - timedelta(days=365),
        last_reinforced_at=NOW - timedelta(days=1),
    )
    assert recency_factor(restated, at=NOW) > recency_factor(stated_long_ago, at=NOW)
    # …without rewriting when the fact became true.
    assert restated.valid_from == stated_long_ago.valid_from


def test_expired_fact_decays_from_when_it_stopped_being_true() -> None:
    expired = _mem(
        status="EXPIRED",
        valid_from=NOW - timedelta(days=900),
        valid_until=NOW - timedelta(days=2),
    )
    assert reference_time(expired) == expired.valid_until
    assert recency_factor(expired, at=NOW) > 0.9


def test_future_timestamp_cannot_score_above_one() -> None:
    """Clock skew must not produce a negative exponent and a runaway score."""
    skewed = _mem(valid_from=NOW + timedelta(days=5))
    assert recency_factor(skewed, at=NOW) == 1.0


def test_missing_timestamps_do_not_invent_decay() -> None:
    # Built directly: _mem's `or` default would substitute a timestamp for None.
    undated = Memory(id=uuid4(), user_id=USER, subject="user", predicate="lives_in",
                     object="Berlin", valid_from=None)
    assert reference_time(undated) is None
    assert recency_factor(undated, at=NOW) == 1.0


# --- the composed score ----------------------------------------------------


def test_score_is_the_product_of_the_three_terms() -> None:
    memory = _mem(valid_from=NOW - timedelta(days=180))
    scored = score_memory(memory, 0.8, at=NOW)
    assert scored.score == pytest.approx(
        scored.similarity * scored.status_weight * scored.recency
    )
    assert scored.score == pytest.approx(0.8 * 1.0 * 0.5, abs=1e-3)


def test_negative_similarity_is_floored_at_zero() -> None:
    """A negative cosine would flip the product's sign and top the ranking."""
    scored = score_memory(_mem(), -0.4, at=NOW)
    assert scored.similarity == 0.0
    assert scored.score == 0.0


def test_current_fact_beats_its_superseded_predecessor() -> None:
    """The core claim: 'where do I live' answers Berlin, not Delhi."""
    berlin = _mem("Berlin", valid_from=NOW - timedelta(days=10))
    delhi = _mem(
        "Delhi",
        status="EXPIRED",
        valid_from=NOW - timedelta(days=400),
        valid_until=NOW - timedelta(days=10),
    )
    # Delhi is even slightly the better textual match, and still loses.
    ranked = rerank([(berlin, 0.80), (delhi, 0.85)], at=NOW, limit=2)
    assert ranked[0].object == "Berlin"
    assert ranked[1].object == "Delhi"


def test_superseded_fact_stays_retrievable() -> None:
    delhi = _mem("Delhi", status="EXPIRED", valid_until=NOW - timedelta(days=10))
    ranked = rerank([(delhi, 0.85)], at=NOW, limit=5)
    assert len(ranked) == 1
    assert ranked[0].score > 0


def test_raising_expired_weight_surfaces_history() -> None:
    """How a 'what did I used to...' query is served by the same formula."""
    berlin = _mem("Berlin", valid_from=NOW - timedelta(days=10))
    delhi = _mem(
        "Delhi",
        status="EXPIRED",
        valid_from=NOW - timedelta(days=400),
        valid_until=NOW - timedelta(days=10),
    )
    ranked = rerank([(berlin, 0.80), (delhi, 0.85)], at=NOW, limit=2, expired_weight=1.0)
    assert ranked[0].object == "Delhi"


def test_zero_lambda_disables_decay_entirely() -> None:
    old = _mem(valid_from=NOW - timedelta(days=3650))
    scored = score_memory(old, 0.9, at=NOW, lambda_per_day=0.0)
    assert scored.recency == 1.0
    assert scored.score == pytest.approx(0.9)


def test_rerank_returns_at_most_limit_sorted_descending() -> None:
    pool = [(_mem(f"City{i}"), 0.5 + i / 100) for i in range(20)]
    ranked = rerank(pool, at=NOW, limit=5)
    assert len(ranked) == 5
    assert [r.score for r in ranked] == sorted((r.score for r in ranked), reverse=True)


def test_components_are_returned_for_debugging() -> None:
    scored = score_memory(_mem(), 0.7, at=NOW)
    assert {"similarity", "status_weight", "recency", "score"} <= set(
        scored.model_dump().keys()
    )


# --- over-fetch ------------------------------------------------------------


def test_pool_is_larger_than_the_requested_limit() -> None:
    """Top-K by cosine is not top-K by score, so re-ranking needs slack."""
    assert candidate_pool_size(10) > 10
    assert candidate_pool_size(1) >= 40


def test_lambda_default_matches_a_180_day_half_life() -> None:
    assert DEFAULT_LAMBDA_PER_DAY == pytest.approx(math.log(2) / 180)


# --- route -----------------------------------------------------------------


class StubEmbeddings:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []

    async def embed_documents(self, texts):
        self.calls.append(texts)
        if self.fail:
            raise RuntimeError("embedding service down")
        return [[1.0] + [0.0] * 1535 for _ in texts]

    async def embed_query(self, text):
        self.calls.append([text])
        if self.fail:
            raise RuntimeError("embedding service down")
        return [1.0] + [0.0] * 1535


class FakeConn:
    def __init__(self, rows=None) -> None:
        self.rows = rows or []
        self.queries: list[tuple[str, tuple]] = []

    async def fetch(self, sql, *args):
        self.queries.append((" ".join(sql.split()), args))
        return self.rows


def _row(memory: Memory, similarity: float) -> dict:
    data = memory.model_dump()
    data["similarity"] = similarity
    return data


def _client(conn, embeddings) -> httpx.AsyncClient:
    async def conn_override():
        yield conn

    app.dependency_overrides[get_conn] = conn_override
    app.dependency_overrides[get_embeddings] = lambda: embeddings
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


@pytest.mark.asyncio
async def test_retrieve_ranks_current_above_superseded() -> None:
    berlin = _mem("Berlin", valid_from=NOW - timedelta(days=10))
    delhi = _mem(
        "Delhi",
        status="EXPIRED",
        valid_from=NOW - timedelta(days=400),
        valid_until=NOW - timedelta(days=10),
    )
    conn = FakeConn([_row(delhi, 0.85), _row(berlin, 0.80)])
    try:
        async with _client(conn, StubEmbeddings()) as client:
            response = await client.post(
                "/api/retrieve",
                json={"user_id": str(USER), "query": "where do I live?", "limit": 5},
            )
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    assert body["results"][0]["object"] == "Berlin"
    assert body["results"][0]["status_weight"] == 1.0
    assert body["results"][1]["status_weight"] == DEFAULT_EXPIRED_WEIGHT


@pytest.mark.asyncio
async def test_as_of_mode_requires_a_timestamp() -> None:
    conn = FakeConn([])
    try:
        async with _client(conn, StubEmbeddings()) as client:
            response = await client.post(
                "/api/retrieve",
                json={"user_id": str(USER), "query": "x", "mode": "as_of"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert conn.queries == []


@pytest.mark.asyncio
async def test_as_of_mode_filters_to_facts_valid_then() -> None:
    conn = FakeConn([])
    try:
        async with _client(conn, StubEmbeddings()) as client:
            response = await client.post(
                "/api/retrieve",
                json={
                    "user_id": str(USER),
                    "query": "where did I live",
                    "mode": "as_of",
                    "as_of": "2026-01-01T00:00:00Z",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    sql = conn.queries[0][0]
    assert "valid_from <= $3" in sql
    assert "valid_until is null or valid_until > $3" in sql
    assert response.json()["evaluated_at"].startswith("2026-01-01")


@pytest.mark.asyncio
async def test_changes_mode_selects_supersede_chains() -> None:
    conn = FakeConn([])
    try:
        async with _client(conn, StubEmbeddings()) as client:
            response = await client.post(
                "/api/retrieve",
                json={"user_id": str(USER), "query": "what changed", "mode": "changes"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    sql = conn.queries[0][0]
    assert "superseded_by is not null or supersedes is not null" in sql


@pytest.mark.asyncio
async def test_embedding_failure_is_502_not_an_arbitrary_ranking() -> None:
    """With no query vector, every similarity is undefined; say so."""
    conn = FakeConn([])
    try:
        async with _client(conn, StubEmbeddings(fail=True)) as client:
            response = await client.post(
                "/api/retrieve", json={"user_id": str(USER), "query": "x"}
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert conn.queries == []


@pytest.mark.asyncio
async def test_response_reports_the_knobs_it_used() -> None:
    conn = FakeConn([])
    try:
        async with _client(conn, StubEmbeddings()) as client:
            response = await client.post(
                "/api/retrieve",
                json={
                    "user_id": str(USER),
                    "query": "x",
                    "lambda_per_day": 0.5,
                    "expired_weight": 0.9,
                },
            )
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert body["lambda_per_day"] == 0.5
    assert body["expired_weight"] == 0.9
    assert body["candidates_considered"] == 0
