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
    # Populated from Phase 3 onward, once the event log write path exists.
    event_id: UUID | None = None


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


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    phase: int
