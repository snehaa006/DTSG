"""Persistence for the mutable state graph.

Unlike `events`, this table is meant to change — but only in one direction.
Nothing here deletes a row. A fact that stops being true is marked EXPIRED,
stamped with `valid_until`, and linked to whatever replaced it, so the history
stays walkable.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from ..schemas import Fact, Memory, MemoryCandidate

_COLUMNS = (
    "id, user_id, subject, predicate, object, status, confidence, "
    "source_event, supersedes, superseded_by, valid_from, valid_until"
)


def to_pgvector(values: list[float]) -> str:
    """Render an embedding in pgvector's literal form.

    asyncpg has no codec for the `vector` type, so the value travels as text and
    is cast in SQL (`$n::vector`). Registering a codec would be tidier but ties
    us to asyncpg internals for no behavioural gain.
    """
    return "[" + ",".join(repr(float(v)) for v in values) + "]"


def embedding_text(fact: Fact) -> str:
    """The string an embedding is computed over.

    Predicates are stored snake_case for exact matching, but underscores are not
    something an embedding model has much use for — `lives_in` embeds better as
    `lives in`.
    """
    return f"{fact.subject} {fact.predicate.replace('_', ' ')} {fact.object}"


def _to_memory(row: asyncpg.Record) -> Memory:
    return Memory(**{k: row[k] for k in Memory.model_fields if k in row})


async def insert_memory(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    fact: Fact,
    embedding: list[float] | None,
    source_event: UUID | None,
    supersedes: UUID | None = None,
) -> Memory:
    row = await conn.fetchrow(
        f"""
        insert into memories
            (user_id, subject, predicate, object, confidence, embedding,
             source_event, supersedes)
        values ($1, $2, $3, $4, $5, $6::vector, $7, $8)
        returning {_COLUMNS}
        """,
        user_id,
        fact.subject,
        fact.predicate,
        fact.object,
        fact.confidence,
        to_pgvector(embedding) if embedding is not None else None,
        source_event,
        supersedes,
    )
    return _to_memory(row)


async def find_candidates(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    fact: Fact,
    embedding: list[float] | None,
    limit: int = 8,
    min_similarity: float = 0.55,
) -> list[MemoryCandidate]:
    """Find ACTIVE memories the new fact might conflict with.

    Two arms, because they fail in opposite directions:

    * **Exact (subject, predicate)** — high precision, and the reason Phase 2
      normalizes both. Always included regardless of vector distance: if the
      user already has a `lives_in` fact, a new `lives_in` fact must be
      considered even when the two cities embed far apart.
    * **Vector similarity** — catches overlap the exact arm misses, e.g. an
      older `works_at` against a new `works_as`. Floored at `min_similarity`
      because feeding unrelated memories to the classifier wastes tokens and
      invites false SUPERSEDE calls.
    """
    exact = await conn.fetch(
        f"""
        select {_COLUMNS}, 1.0::float8 as similarity
        from memories
        where user_id = $1 and status = 'ACTIVE'
          and subject = $2 and predicate = $3
        order by valid_from desc
        limit $4
        """,
        user_id,
        fact.subject,
        fact.predicate,
        limit,
    )

    similar: list[asyncpg.Record] = []
    if embedding is not None:
        similar = await conn.fetch(
            f"""
            select {_COLUMNS}, 1 - (embedding <=> $2::vector) as similarity
            from memories
            where user_id = $1 and status = 'ACTIVE'
              and embedding is not null
              and 1 - (embedding <=> $2::vector) >= $3
            order by embedding <=> $2::vector
            limit $4
            """,
            user_id,
            to_pgvector(embedding),
            min_similarity,
            limit,
        )

    # Exact matches first so they survive the cap when both arms are full.
    seen: set[UUID] = set()
    candidates: list[MemoryCandidate] = []
    for row in [*exact, *similar]:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        candidates.append(
            MemoryCandidate(
                **{k: row[k] for k in Memory.model_fields if k in row},
                similarity=float(row["similarity"]),
            )
        )
    return candidates[:limit]


async def expire_memories(
    conn: asyncpg.Connection,
    *,
    memory_ids: list[UUID],
    superseded_by: UUID,
) -> int:
    """Mark memories EXPIRED and point them at their replacement.

    `valid_until` is set from the database clock for the same reason event
    timestamps are: one clock, not one per Render instance.

    The `status = 'ACTIVE'` guard makes this idempotent — a retry cannot
    re-expire a row and overwrite the `superseded_by` written by the first
    attempt, which would corrupt the chain.
    """
    if not memory_ids:
        return 0
    result = await conn.execute(
        """
        update memories
        set status = 'EXPIRED', valid_until = now(), superseded_by = $2
        where id = any($1::uuid[]) and status = 'ACTIVE'
        """,
        memory_ids,
        superseded_by,
    )
    return int(result.split()[-1])


async def reinforce_memory(
    conn: asyncpg.Connection, *, memory_id: UUID, amount: float = 0.3
) -> Memory | None:
    """Raise a memory's confidence after the user restated it.

    Moves a fraction of the remaining distance to 1.0 rather than adding a fixed
    step, so repeated confirmations approach certainty without ever claiming it,
    and a fact asserted ten times does not overflow the scale.

    Deliberately does not touch `valid_from`: that records when the fact became
    true, not when it was last mentioned. Phase 5's recency decay will need a
    separate `last_reinforced_at` column if reinforcement should count as
    freshness.
    """
    row = await conn.fetchrow(
        f"""
        update memories
        set confidence = least(1.0, confidence + (1.0 - confidence) * $2)
        where id = $1 and status = 'ACTIVE'
        returning {_COLUMNS}
        """,
        memory_id,
        amount,
    )
    return _to_memory(row) if row is not None else None


async def list_memories(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    status: str | None = None,
    limit: int = 100,
) -> list[Memory]:
    if status is not None:
        rows = await conn.fetch(
            f"""
            select {_COLUMNS} from memories
            where user_id = $1 and status = $2
            order by valid_from desc, id desc
            limit $3
            """,
            user_id,
            status,
            limit,
        )
    else:
        rows = await conn.fetch(
            f"""
            select {_COLUMNS} from memories
            where user_id = $1
            order by valid_from desc, id desc
            limit $2
            """,
            user_id,
            limit,
        )
    return [_to_memory(row) for row in rows]


async def get_memory(conn: asyncpg.Connection, memory_id: UUID) -> Memory | None:
    row = await conn.fetchrow(f"select {_COLUMNS} from memories where id = $1", memory_id)
    return _to_memory(row) if row is not None else None
