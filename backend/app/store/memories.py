"""Persistence for the mutable state graph.

Unlike `events`, this table is meant to change — but only in one direction.
Nothing here deletes a row. A fact that stops being true is marked EXPIRED,
stamped with `valid_until`, and linked to whatever replaced it, so the history
stays walkable.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from ..schemas import Fact, Memory, MemoryCandidate

_COLUMNS = (
    "id, user_id, subject, predicate, object, status, confidence, "
    "source_event, supersedes, superseded_by, valid_from, valid_until, "
    "last_reinforced_at"
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

    Stamps `last_reinforced_at` (migration 0004) but deliberately leaves
    `valid_from` alone: that records when the fact became true, not when it was
    last mentioned. Retrieval decays from the former, so restating a fact makes
    it rank as fresh without rewriting when it started.
    """
    row = await conn.fetchrow(
        f"""
        update memories
        set confidence = least(1.0, confidence + (1.0 - confidence) * $2),
            last_reinforced_at = now()
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


async def search_candidates(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    embedding: list[float],
    pool_size: int,
    mode: str = "now",
    as_of: datetime | None = None,
) -> list[tuple[Memory, float]]:
    """Fetch the ANN candidate pool that re-ranking will score.

    Returns `(memory, cosine_similarity)` pairs ordered by vector distance. This
    is only the *first* stage: the final ordering comes from the temporal score
    in `app.retrieval`, which the database cannot index over.

    Modes differ in what is eligible, not in how it is scored:

    * ``now``     — everything, including superseded facts. They are discounted
                    by `status_weight`, not filtered out, which is what keeps a
                    stale answer reachable when nothing current fits.
    * ``as_of``   — only facts that held at that instant: recorded by then and
                    not yet expired. A fact superseded last week is eligible for
                    a query about last month.
    * ``changes`` — only rows in a supersede chain, i.e. the graph's diffs.

    Rows with a null embedding are unreachable in the similarity modes; that is
    the cost of the Phase 4 decision to store a fact rather than drop it when
    the embedding service is down, and a backfill fixes it. ``changes`` does not
    rank by similarity, so it includes them.
    """
    vector = to_pgvector(embedding)

    if mode == "as_of":
        if as_of is None:
            raise ValueError("as_of mode requires an as_of timestamp")
        return _pairs(
            await conn.fetch(
                f"""
                select {_COLUMNS}, 1 - (embedding <=> $2::vector) as similarity
                from memories
                where user_id = $1
                  and embedding is not null
                  and valid_from <= $3
                  and (valid_until is null or valid_until > $3)
                order by embedding <=> $2::vector
                limit $4
                """,
                user_id,
                vector,
                as_of,
                pool_size,
            )
        )

    if mode == "changes":
        # Ordered by when the change happened, not by similarity: "what changed"
        # is a question about the timeline, and an empty-ish query should still
        # return the most recent transitions rather than arbitrary near matches.
        return _pairs(
            await conn.fetch(
                f"""
                select {_COLUMNS},
                       case when embedding is null then 0.0::float8
                            else 1 - (embedding <=> $2::vector) end as similarity
                from memories
                where user_id = $1
                  and (superseded_by is not null or supersedes is not null)
                order by coalesce(valid_until, valid_from) desc
                limit $3
                """,
                user_id,
                vector,
                pool_size,
            )
        )

    return _pairs(
        await conn.fetch(
            f"""
            select {_COLUMNS}, 1 - (embedding <=> $2::vector) as similarity
            from memories
            where user_id = $1 and embedding is not null
            order by embedding <=> $2::vector
            limit $3
            """,
            user_id,
            vector,
            pool_size,
        )
    )


async def search_naive(
    conn: asyncpg.Connection,
    *,
    user_id: UUID,
    embedding: list[float],
    limit: int,
) -> list[tuple[Memory, float]]:
    """Top-K by cosine similarity alone — the Phase 7 baseline.

    No status filter and no temporal term, which is the point: this is what
    retrieval looks like when the store has no concept of a fact ceasing to be
    true. Superseded rows compete on equal footing with the ones that replaced
    them.
    """
    return _pairs(
        await conn.fetch(
            f"""
            select {_COLUMNS}, 1 - (embedding <=> $2::vector) as similarity
            from memories
            where user_id = $1 and embedding is not null
            order by embedding <=> $2::vector
            limit $3
            """,
            user_id,
            to_pgvector(embedding),
            limit,
        )
    )


def _pairs(rows: list[asyncpg.Record]) -> list[tuple[Memory, float]]:
    return [(_to_memory(row), float(row["similarity"])) for row in rows]


async def get_memory(conn: asyncpg.Connection, memory_id: UUID) -> Memory | None:
    row = await conn.fetchrow(f"select {_COLUMNS} from memories where id = $1", memory_id)
    return _to_memory(row) if row is not None else None
