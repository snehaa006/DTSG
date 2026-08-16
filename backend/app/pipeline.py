"""Phase 4 — the ingest pipeline.

    message -> event (committed) -> facts -> candidates -> classification -> writes

**The event is committed before anything derived runs, in its own transaction.**
Extraction and classification both call an LLM, so both can fail on a rate
limit, a timeout, or a bad day. If the whole flow shared one transaction, those
failures would roll back the raw message too — and the raw message is the one
thing that cannot be reconstructed. Memories can always be rebuilt by replaying
events; events cannot be rebuilt from anything. So the log commits first, and a
failure downstream costs derived state, never history.

Each fact is then applied in its own transaction, so one fact's failure does not
undo its predecessors' writes.
"""

from __future__ import annotations

import logging
from uuid import UUID

import asyncpg

from .classifier import Decision, classify
from .extraction import extract_facts
from .llm.base import LLMProvider
from .llm.embeddings import EmbeddingClient
from .schemas import Fact, FactOutcome, IngestResult
from .store import events as events_store
from .store import memories as memories_store

log = logging.getLogger(__name__)


async def _embed_one(embeddings: EmbeddingClient, fact: Fact) -> list[float] | None:
    """Embed a fact, or return None if the embedding service is unavailable.

    A missing embedding degrades candidate search to exact (subject, predicate)
    matching rather than failing the write. That is a real loss of recall, but
    storing the fact with a null embedding keeps the graph moving and the row
    can be backfilled; dropping the fact loses it.
    """
    try:
        vectors = await embeddings.embed([memories_store.embedding_text(fact)])
        return vectors[0] if vectors else None
    except Exception:  # noqa: BLE001 - degrade, don't fail the write
        log.warning("embedding failed; falling back to exact matching", exc_info=True)
        return None


async def _apply(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    fact: Fact,
    embedding: list[float] | None,
    event_id: UUID,
    decision: Decision,
) -> FactOutcome:
    """Write one classified fact. Runs inside a transaction."""
    outcome = FactOutcome(
        fact=fact, resolution=decision.resolution, reasoning=decision.reasoning
    )

    if decision.resolution == "REINFORCE":
        target = decision.targets[0]
        reinforced = await memories_store.reinforce_memory(conn, memory_id=target.id)
        if reinforced is not None:
            outcome.reinforced_memory_id = reinforced.id
            outcome.memory_id = reinforced.id
            return outcome
        # The row stopped being ACTIVE between classification and this write —
        # a concurrent message superseded it. Storing the fact is then correct:
        # the user just restated something that had been expired out from under
        # them, and dropping it would lose a true fact.
        log.info("reinforce target %s no longer active; inserting instead", target.id)

    supersedes = decision.targets[0].id if decision.resolution == "SUPERSEDE" else None

    memory = await memories_store.insert_memory(
        conn,
        user_id=user_id,
        fact=fact,
        embedding=embedding,
        source_event=event_id,
        # `memories.supersedes` holds a single uuid, so when a fact invalidates
        # several it records the closest one. The complete edge set lives on the
        # other side: every expired row gets `superseded_by` pointing here, so
        # `superseded_by` is the authoritative link and what Phase 6 should walk.
        supersedes=supersedes,
    )
    outcome.memory_id = memory.id

    if decision.resolution == "SUPERSEDE":
        target_ids = [t.id for t in decision.targets]
        await memories_store.expire_memories(
            conn, memory_ids=target_ids, superseded_by=memory.id
        )
        outcome.expired_memory_ids = target_ids

    return outcome


async def ingest_message(
    conn: asyncpg.Connection,
    *,
    llm: LLMProvider,
    embeddings: EmbeddingClient,
    user_id: UUID,
    message: str,
) -> IngestResult:
    # 1. Log the raw message. Committed on its own — see the module docstring.
    async with conn.transaction():
        event = await events_store.insert_event(
            conn, user_id=user_id, raw_text=message
        )

    result = IngestResult(event_id=event.id)

    # 2. Everything downstream is derived and therefore rebuildable, so a
    #    failure here is reported without disturbing the logged event.
    try:
        facts = await extract_facts(llm, message)
    except Exception as exc:  # noqa: BLE001
        log.warning("extraction failed for event %s", event.id, exc_info=True)
        result.error = f"extraction failed: {exc}"
        return result

    for fact in facts:
        try:
            embedding = await _embed_one(embeddings, fact)
            candidates = await memories_store.find_candidates(
                conn, user_id=user_id, fact=fact, embedding=embedding
            )
            decision = await classify(llm, fact, candidates)

            # Insert-and-expire must be atomic: a crash between them would
            # either leave two contradictory ACTIVE facts or expire the old one
            # with nothing replacing it.
            async with conn.transaction():
                outcome = await _apply(
                    conn,
                    user_id=user_id,
                    fact=fact,
                    embedding=embedding,
                    event_id=event.id,
                    decision=decision,
                )
            result.outcomes.append(outcome)
        except Exception as exc:  # noqa: BLE001 - one bad fact shouldn't sink the rest
            log.warning(
                "failed to apply fact (%s, %s, %s)",
                fact.subject,
                fact.predicate,
                fact.object,
                exc_info=True,
            )
            result.error = f"partial failure: {exc}"

    return result
