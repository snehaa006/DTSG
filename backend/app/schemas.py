"""Wire types shared by the API and the React client.

Phase 1 only needs the chat request/response pair and the health payload. The
memory/event shapes are declared here already because Phases 3-6 all read them,
and keeping them in one file makes the TypeScript mirror in
`frontend/src/lib/types.ts` easy to keep honest.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

MemoryStatus = Literal["ACTIVE", "EXPIRED"]
Resolution = Literal["REINFORCE", "ADDITIVE", "SUPERSEDE"]


class ChatRequest(BaseModel):
    user_id: UUID
    message: str = Field(min_length=1, max_length=8000)


class ChatResponse(BaseModel):
    reply: str
    # The append-only event this message was recorded as.
    event_id: UUID
    # What the write pipeline did with each extracted fact. Declared after
    # FactOutcome via the forward reference resolved at the end of this module.
    outcomes: list["FactOutcome"] = Field(default_factory=list)
    error: str | None = None


class Event(BaseModel):
    id: UUID
    user_id: UUID
    raw_text: str
    timestamp: datetime


class Fact(BaseModel):
    """An extracted subject-predicate-object triple, before it is persisted."""

    subject: str
    predicate: str
    object: str
    confidence: float = 0.8


class EventListResponse(BaseModel):
    events: list[Event]
    # Cursor for the next page: pass these back as before_timestamp/before_id.
    # Null when this is the last page.
    next_before_timestamp: datetime | None = None
    next_before_id: UUID | None = None


class ExtractRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class ExtractResponse(BaseModel):
    facts: list[Fact]
    count: int


class Memory(BaseModel):
    id: UUID
    user_id: UUID
    subject: str | None = None
    predicate: str | None = None
    object: str | None = None
    status: MemoryStatus = "ACTIVE"
    confidence: float = 0.8
    source_event: UUID | None = None
    supersedes: UUID | None = None
    superseded_by: UUID | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    last_reinforced_at: datetime | None = None


class MemoryCandidate(Memory):
    """A memory offered to the classifier, with how close it was judged to be."""

    similarity: float


class FactOutcome(BaseModel):
    """What the write pipeline did with one extracted fact."""

    fact: Fact
    resolution: Resolution
    reasoning: str
    memory_id: UUID | None = None
    expired_memory_ids: list[UUID] = Field(default_factory=list)
    reinforced_memory_id: UUID | None = None


class IngestResult(BaseModel):
    event_id: UUID
    outcomes: list[FactOutcome] = Field(default_factory=list)
    # Set when the event was logged but fact processing failed. The event still
    # persists; the derived state can be rebuilt from it later.
    error: str | None = None


class MemoryListResponse(BaseModel):
    memories: list[Memory]


class ScoredMemory(Memory):
    """A memory with the three scoring terms kept separate.

    The components are returned, not just the product, because a ranking you
    cannot decompose is one you cannot debug: when a stale fact outranks a
    current one, the only useful question is which term caused it.
    """

    similarity: float
    status_weight: float
    recency: float
    score: float


class RetrieveRequest(BaseModel):
    user_id: UUID
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=10, ge=1, le=100)
    mode: Literal["now", "as_of", "changes"] = "now"
    # Required for mode="as_of": the moment to evaluate the graph at.
    as_of: datetime | None = None
    # Scoring knobs, exposed so a test set can be swept without a redeploy.
    lambda_per_day: float | None = Field(default=None, ge=0.0)
    expired_weight: float | None = Field(default=None, ge=0.0, le=1.0)


class RetrieveResponse(BaseModel):
    results: list[ScoredMemory]
    mode: str
    # The instant scoring was evaluated at: `as_of` when given, else now.
    evaluated_at: datetime
    # How many rows re-ranking chose from. If this equals the pool size, the
    # pool was saturated and a higher over-fetch might surface something better.
    candidates_considered: int
    lambda_per_day: float
    expired_weight: float


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    phase: int


# ChatResponse references FactOutcome, which is defined further down the module.
ChatResponse.model_rebuild()
