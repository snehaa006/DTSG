"""Phase 5 — temporal retrieval.

    score = cosine_similarity * status_weight * exp(-lambda * time_delta)

Three ideas, one multiplication:

* **cosine_similarity** — does this fact relate to the question at all
* **status_weight** — is it still true (1.0) or was it superseded (~0.15)
* **exp(-lambda * time_delta)** — how stale is it

The status term is what separates DTSG from ordinary RAG. A superseded fact is
not deleted and not excluded; it is *discounted*. So "where do I live?" ranks
Berlin far above Delhi without Delhi ever becoming unreachable — and a question
about the past can raise its weight instead of querying a different store.

Everything here is a pure function of already-fetched rows. Scoring in Python
rather than SQL is the re-ranking stage: no index can be built over a formula
whose terms depend on query time and caller-supplied constants, so the database
does the part it is good at (approximate-nearest-neighbour over the HNSW index)
and this module does the rest.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from .schemas import Memory, ScoredMemory

Mode = Literal["now", "as_of", "changes"]

# Weight applied to superseded facts. Low enough that a stale fact never
# outranks its live replacement on similar text, high enough that it stays
# reachable when nothing current answers the question.
DEFAULT_EXPIRED_WEIGHT = 0.15

# Decay constant, per day. Derived from a 180-day half-life: ln(2)/180. Facts
# people state about themselves age slowly — a job or a city stays true for
# months or years — so aggressive decay would bury correct answers faster than
# they actually go stale.
DEFAULT_LAMBDA_PER_DAY = math.log(2) / 180

# The database can only pre-filter and order by vector distance. Because the
# status and recency terms reorder results heavily, the top-K by cosine alone is
# not the top-K by score — so fetch several times what was asked for and let
# re-ranking choose. Too small a factor silently drops a memory that would have
# won on the full formula.
DEFAULT_OVER_FETCH = 6
MIN_CANDIDATE_POOL = 40


def status_weight(
    memory: Memory,
    *,
    at: datetime,
    expired_weight: float = DEFAULT_EXPIRED_WEIGHT,
) -> float:
    """Weight a memory by whether it held at time `at`.

    Evaluated against `at` rather than the stored `status` column, because those
    disagree for point-in-time queries: a fact expired last week was perfectly
    true a month ago, and asking "what was true in June" should treat it as
    current, not as a discounted leftover.
    """
    started = memory.valid_from
    if started is not None and started > at:
        # Recorded after the moment being asked about; it had not happened yet.
        return expired_weight
    ended = memory.valid_until
    still_held = ended is None or ended > at
    return 1.0 if still_held else expired_weight


def reference_time(memory: Memory) -> datetime | None:
    """The instant recency is measured from.

    * ACTIVE — when the fact was last restated, else when it became true.
      Reinforcement counting as freshness is the point of `last_reinforced_at`
      (migration 0004); without it, saying something again has no effect on
      whether it can be retrieved.
    * EXPIRED — when it stopped being true. Among superseded facts, the ones
      that changed recently are the interesting ones, which is what makes
      "what changed lately" answerable by the same formula.
    """
    if memory.valid_until is not None:
        return memory.valid_until
    return memory.last_reinforced_at or memory.valid_from


def recency_factor(
    memory: Memory,
    *,
    at: datetime,
    lambda_per_day: float = DEFAULT_LAMBDA_PER_DAY,
) -> float:
    ref = reference_time(memory)
    if ref is None:
        # No temporal anchor at all; don't invent decay for it.
        return 1.0
    # Clamped at zero so a fact timestamped slightly ahead of `at` (clock skew,
    # or an as_of between valid_from and now) cannot score above 1.0 and
    # outrank everything through a negative exponent.
    delta_days = max(0.0, (at - ref).total_seconds() / 86400.0)
    return math.exp(-lambda_per_day * delta_days)


def score_memory(
    memory: Memory,
    similarity: float,
    *,
    at: datetime,
    lambda_per_day: float = DEFAULT_LAMBDA_PER_DAY,
    expired_weight: float = DEFAULT_EXPIRED_WEIGHT,
) -> ScoredMemory:
    weight = status_weight(memory, at=at, expired_weight=expired_weight)
    recency = recency_factor(memory, at=at, lambda_per_day=lambda_per_day)
    # Cosine similarity runs [-1, 1]; a negative would flip the sign of the
    # whole product and turn an irrelevant memory into a top hit.
    similarity = max(0.0, similarity)
    return ScoredMemory(
        **memory.model_dump(),
        similarity=similarity,
        status_weight=weight,
        recency=recency,
        score=similarity * weight * recency,
    )


def rerank(
    scored: list[tuple[Memory, float]],
    *,
    at: datetime,
    limit: int,
    lambda_per_day: float = DEFAULT_LAMBDA_PER_DAY,
    expired_weight: float = DEFAULT_EXPIRED_WEIGHT,
) -> list[ScoredMemory]:
    """Score a candidate pool and return the best `limit` by the full formula."""
    results = [
        score_memory(
            memory,
            similarity,
            at=at,
            lambda_per_day=lambda_per_day,
            expired_weight=expired_weight,
        )
        for memory, similarity in scored
    ]
    # Ties broken by reference time so equal-scoring facts come back newest
    # first, which is both stable and the more useful order.
    results.sort(
        key=lambda r: (r.score, reference_time(r) or datetime.min.replace(tzinfo=at.tzinfo)),
        reverse=True,
    )
    return results[:limit]


def candidate_pool_size(limit: int, over_fetch: int = DEFAULT_OVER_FETCH) -> int:
    return max(MIN_CANDIDATE_POOL, limit * over_fetch)
