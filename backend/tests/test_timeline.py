"""Tests for supersede-chain reconstruction."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import httpx
import pytest

from app.deps import get_conn
from app.main import app
from app.schemas import Memory
from app.timeline import build_chains

USER = UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def _mem(obj, *, days_ago, status="ACTIVE", superseded_by=None, supersedes=None,
         predicate="lives_in") -> Memory:
    return Memory(
        id=uuid4(),
        user_id=USER,
        subject="user",
        predicate=predicate,
        object=obj,
        status=status,
        valid_from=NOW - timedelta(days=days_ago),
        valid_until=None if status == "ACTIVE" else NOW - timedelta(days=days_ago - 1),
        superseded_by=superseded_by,
        supersedes=supersedes,
    )


def _chain_of_three():
    """Delhi -> Berlin -> Lisbon."""
    lisbon = _mem("Lisbon", days_ago=5)
    berlin = _mem("Berlin", days_ago=100, status="EXPIRED", superseded_by=lisbon.id,
                  supersedes=None)
    delhi = _mem("Delhi", days_ago=400, status="EXPIRED", superseded_by=berlin.id)
    lisbon.supersedes = berlin.id
    berlin.supersedes = delhi.id
    return [delhi, berlin, lisbon]


def test_chain_is_ordered_oldest_first() -> None:
    chains = build_chains(_chain_of_three())
    assert len(chains) == 1
    assert [e.object for e in chains[0].entries] == ["Delhi", "Berlin", "Lisbon"]


def test_chain_reports_current_state_and_revision_count() -> None:
    chain = build_chains(_chain_of_three())[0]
    assert chain.is_current is True
    assert chain.revisions == 2
    assert chain.predicate == "lives_in"


def test_input_order_does_not_matter() -> None:
    """Rows arrive newest-first from the store; the chain must not depend on it."""
    memories = _chain_of_three()
    forward = build_chains(memories)[0]
    backward = build_chains(list(reversed(memories)))[0]
    assert [e.object for e in forward.entries] == [e.object for e in backward.entries]


def test_unsuperseded_fact_is_a_chain_of_one() -> None:
    """So the UI needs one rendering path, not two."""
    chains = build_chains([_mem("Berlin", days_ago=5)])
    assert len(chains) == 1
    assert chains[0].revisions == 0
    assert chains[0].is_current is True


def test_independent_facts_form_separate_chains() -> None:
    memories = [
        _mem("Berlin", days_ago=5),
        _mem("Hindi", days_ago=20, predicate="speaks"),
        _mem("English", days_ago=30, predicate="speaks"),
    ]
    chains = build_chains(memories)
    assert len(chains) == 3
    assert all(c.revisions == 0 for c in chains)


def test_chains_are_ordered_by_most_recent_activity() -> None:
    old_chain = _mem("Rex", days_ago=300, predicate="has_pet")
    recent = _mem("Berlin", days_ago=2)
    chains = build_chains([old_chain, recent])
    assert chains[0].entries[-1].object == "Berlin"


def test_ended_chain_is_marked_not_current() -> None:
    """A fact expired with no replacement present in this page."""
    orphan = _mem("Acme", days_ago=50, status="EXPIRED", predicate="works_at",
                  superseded_by=uuid4())
    chain = build_chains([orphan])[0]
    assert chain.is_current is False


def test_dangling_link_stops_the_walk_without_inventing_entries() -> None:
    missing = uuid4()
    delhi = _mem("Delhi", days_ago=400, status="EXPIRED", superseded_by=missing)
    chains = build_chains([delhi])
    assert len(chains) == 1
    assert [e.object for e in chains[0].entries] == ["Delhi"]


def test_multiple_facts_superseded_by_one_are_all_reachable() -> None:
    """Phase 4 can expire several rows against a single replacement."""
    lisbon = _mem("Lisbon", days_ago=5)
    a = _mem("Delhi", days_ago=400, status="EXPIRED", superseded_by=lisbon.id)
    b = _mem("Berlin", days_ago=200, status="EXPIRED", superseded_by=lisbon.id)
    chains = build_chains([a, b, lisbon])

    seen = {e.object for c in chains for e in c.entries}
    assert seen == {"Delhi", "Berlin", "Lisbon"}
    # Lisbon is the tail of one chain; the other old row keeps its own chain
    # rather than being dropped for having a shared target.
    assert sum(len(c.entries) for c in chains) == 3


def test_cycle_does_not_hang_and_rows_are_still_surfaced() -> None:
    """A corrupt link is a bug to see, not a fact to silently drop."""
    a = _mem("A", days_ago=10, status="EXPIRED")
    b = _mem("B", days_ago=5, status="EXPIRED")
    a.superseded_by = b.id
    b.superseded_by = a.id

    chains = build_chains([a, b])
    seen = {e.object for c in chains for e in c.entries}
    assert seen == {"A", "B"}


def test_empty_input_gives_empty_output() -> None:
    assert build_chains([]) == []


# --- route -----------------------------------------------------------------


class FakeConn:
    def __init__(self, rows) -> None:
        self.rows = rows

    async def fetch(self, sql, *args):
        return self.rows


@pytest.mark.asyncio
async def test_timeline_route_returns_chains() -> None:
    rows = [m.model_dump() for m in _chain_of_three()]
    conn = FakeConn(rows)

    async def override():
        yield conn

    app.dependency_overrides[get_conn] = override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            response = await client.get(f"/api/timeline?user_id={USER}")
    finally:
        app.dependency_overrides.clear()

    body = response.json()
    assert response.status_code == 200
    assert len(body["chains"]) == 1
    assert [e["object"] for e in body["chains"][0]["entries"]] == [
        "Delhi",
        "Berlin",
        "Lisbon",
    ]
    assert body["chains"][0]["revisions"] == 2
