"""Phase 6 — reconstruct supersede chains for display.

Memories store their history as links: an expired row's `superseded_by` points
at whatever replaced it. That is the right shape for writing (each supersede
touches two rows) and the wrong shape for reading — a UI wants the whole story
of one fact in order, not a bag of rows pointing at each other.

This walks the links into chains, oldest first. It is pure: the caller fetches
the rows, this arranges them.

`superseded_by` is used as the edge rather than `supersedes` because it is the
complete one. When a single new fact invalidates several old ones, each of them
records the replacement, while the new row's `supersedes` column can only hold
one of the ids it replaced (see Phase 4).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from .schemas import Memory, TimelineChain


def _sort_key(memory: Memory) -> datetime:
    return memory.valid_from or datetime.min.replace(tzinfo=timezone.utc)


def build_chains(memories: list[Memory]) -> list[TimelineChain]:
    """Group memories into supersede chains, newest activity first.

    A fact that was never superseded is a chain of one — that keeps the caller
    from needing two rendering paths for "history" and "just a fact".
    """
    by_id: dict[UUID, Memory] = {m.id: m for m in memories}

    # A node has a predecessor if some other row was superseded *by* it.
    has_predecessor = {
        m.superseded_by for m in memories if m.superseded_by is not None
    }
    heads = [m for m in memories if m.id not in has_predecessor]

    chains: list[TimelineChain] = []
    visited: set[UUID] = set()

    for head in sorted(heads, key=_sort_key):
        entries: list[Memory] = []
        cursor: Memory | None = head
        while cursor is not None and cursor.id not in visited:
            visited.add(cursor.id)
            entries.append(cursor)
            nxt = cursor.superseded_by
            # A link can dangle when the replacement is outside this page of
            # results; stop rather than inventing an entry.
            cursor = by_id.get(nxt) if nxt is not None else None

        if entries:
            chains.append(_to_chain(entries))

    # Anything left is in a cycle, which the write path should make impossible.
    # Surface those rows as single-entry chains rather than dropping them: a
    # corrupt link is a bug to see, not a fact to hide.
    for memory in memories:
        if memory.id not in visited:
            visited.add(memory.id)
            chains.append(_to_chain([memory]))

    chains.sort(key=lambda c: _sort_key(c.entries[-1]), reverse=True)
    return chains


def _to_chain(entries: list[Memory]) -> TimelineChain:
    current = entries[-1]
    return TimelineChain(
        subject=current.subject,
        predicate=current.predicate,
        entries=entries,
        is_current=current.status == "ACTIVE",
        revisions=len(entries) - 1,
    )
