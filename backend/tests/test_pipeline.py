"""Tests for the ingest pipeline's write semantics and failure behaviour."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app import pipeline
from app.classifier import Decision
from app.schemas import Fact, Memory, MemoryCandidate

USER = UUID("00000000-0000-0000-0000-000000000001")
EVENT_ID = uuid4()


def _fact(obj="Berlin"):
    return Fact(subject="user", predicate="lives_in", object=obj, confidence=0.9)


def _candidate(obj="Delhi"):
    return MemoryCandidate(
        id=uuid4(),
        user_id=USER,
        subject="user",
        predicate="lives_in",
        object=obj,
        status="ACTIVE",
        confidence=0.8,
        valid_from=datetime.now(timezone.utc),
        similarity=0.9,
    )


def _memory(**kw):
    base = dict(
        id=uuid4(),
        user_id=USER,
        subject="user",
        predicate="lives_in",
        object="Berlin",
        status="ACTIVE",
        confidence=0.9,
    )
    base.update(kw)
    return Memory(**base)


class FakeTx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeConn:
    def transaction(self):
        return FakeTx()


class Recorder:
    """Stands in for the memories store, recording what the pipeline asked for."""

    def __init__(self, reinforce_result=_memory()) -> None:
        self.inserted: list[dict] = []
        self.expired: list[dict] = []
        self.reinforced: list[UUID] = []
        self._reinforce_result = reinforce_result

    async def insert(self, conn, **kw):
        self.inserted.append(kw)
        return _memory(id=kw.get("_id", uuid4()))

    async def expire(self, conn, *, memory_ids, superseded_by):
        self.expired.append({"ids": memory_ids, "by": superseded_by})
        return len(memory_ids)

    async def reinforce(self, conn, *, memory_id, amount=0.3):
        self.reinforced.append(memory_id)
        return self._reinforce_result


@pytest.fixture
def patched(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(pipeline.memories_store, "insert_memory", rec.insert)
    monkeypatch.setattr(pipeline.memories_store, "expire_memories", rec.expire)
    monkeypatch.setattr(pipeline.memories_store, "reinforce_memory", rec.reinforce)
    return rec


# --- per-resolution write semantics ----------------------------------------


@pytest.mark.asyncio
async def test_additive_inserts_without_touching_anything(patched) -> None:
    outcome = await pipeline._apply(
        FakeConn(),
        user_id=USER,
        fact=_fact(),
        embedding=[0.1],
        event_id=EVENT_ID,
        decision=Decision(resolution="ADDITIVE", targets=[], reasoning="new"),
    )

    assert len(patched.inserted) == 1
    assert patched.inserted[0]["supersedes"] is None
    assert patched.expired == []
    assert outcome.expired_memory_ids == []


@pytest.mark.asyncio
async def test_supersede_inserts_then_expires_targets(patched) -> None:
    old = _candidate()
    outcome = await pipeline._apply(
        FakeConn(),
        user_id=USER,
        fact=_fact(),
        embedding=[0.1],
        event_id=EVENT_ID,
        decision=Decision(resolution="SUPERSEDE", targets=[old], reasoning="moved"),
    )

    assert len(patched.inserted) == 1
    assert patched.expired[0]["ids"] == [old.id]
    # The expired row points at the newly inserted memory, not at anything else.
    assert patched.expired[0]["by"] == outcome.memory_id
    assert outcome.expired_memory_ids == [old.id]


@pytest.mark.asyncio
async def test_supersede_records_the_closest_target_on_the_new_row(patched) -> None:
    """`supersedes` holds one uuid; `superseded_by` carries the full edge set."""
    first, second = _candidate("Delhi"), _candidate("Mumbai")
    outcome = await pipeline._apply(
        FakeConn(),
        user_id=USER,
        fact=_fact(),
        embedding=[0.1],
        event_id=EVENT_ID,
        decision=Decision(
            resolution="SUPERSEDE", targets=[first, second], reasoning="moved"
        ),
    )

    assert patched.inserted[0]["supersedes"] == first.id
    assert set(patched.expired[0]["ids"]) == {first.id, second.id}
    assert set(outcome.expired_memory_ids) == {first.id, second.id}


@pytest.mark.asyncio
async def test_reinforce_bumps_and_does_not_insert(patched) -> None:
    old = _candidate()
    outcome = await pipeline._apply(
        FakeConn(),
        user_id=USER,
        fact=_fact(),
        embedding=[0.1],
        event_id=EVENT_ID,
        decision=Decision(resolution="REINFORCE", targets=[old], reasoning="same"),
    )

    assert patched.reinforced == [old.id]
    assert patched.inserted == []
    assert outcome.reinforced_memory_id is not None


@pytest.mark.asyncio
async def test_reinforce_falls_back_to_insert_if_target_went_inactive(
    monkeypatch,
) -> None:
    """A concurrent message may expire the target between classify and write;
    dropping the fact would lose something the user just asserted."""
    rec = Recorder(reinforce_result=None)
    monkeypatch.setattr(pipeline.memories_store, "insert_memory", rec.insert)
    monkeypatch.setattr(pipeline.memories_store, "expire_memories", rec.expire)
    monkeypatch.setattr(pipeline.memories_store, "reinforce_memory", rec.reinforce)

    outcome = await pipeline._apply(
        FakeConn(),
        user_id=USER,
        fact=_fact(),
        embedding=[0.1],
        event_id=EVENT_ID,
        decision=Decision(resolution="REINFORCE", targets=[_candidate()], reasoning="x"),
    )

    assert len(rec.inserted) == 1
    assert outcome.memory_id is not None


@pytest.mark.asyncio
async def test_source_event_is_recorded_on_every_insert(patched) -> None:
    await pipeline._apply(
        FakeConn(),
        user_id=USER,
        fact=_fact(),
        embedding=[0.1],
        event_id=EVENT_ID,
        decision=Decision(resolution="ADDITIVE", targets=[], reasoning="new"),
    )
    assert patched.inserted[0]["source_event"] == EVENT_ID


# --- degradation -----------------------------------------------------------


@pytest.mark.asyncio
async def test_embedding_failure_degrades_instead_of_dropping_the_fact() -> None:
    class Broken:
        async def embed(self, texts):
            raise RuntimeError("embedding service down")

    assert await pipeline._embed_one(Broken(), _fact()) is None


@pytest.mark.asyncio
async def test_extraction_failure_leaves_the_event_committed(monkeypatch) -> None:
    """The raw message is the one thing that cannot be reconstructed."""
    event = _memory(id=EVENT_ID)

    async def fake_insert_event(conn, *, user_id, raw_text):
        return type("E", (), {"id": EVENT_ID})()

    async def broken_extract(llm, text):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(pipeline.events_store, "insert_event", fake_insert_event)
    monkeypatch.setattr(pipeline, "extract_facts", broken_extract)

    result = await pipeline.ingest_message(
        FakeConn(), llm=object(), embeddings=object(), user_id=USER, message="hi"
    )

    assert result.event_id == EVENT_ID
    assert result.error is not None and "extraction failed" in result.error
    assert result.outcomes == []
    assert event is not None
