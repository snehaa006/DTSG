"""Chat entry point — now the full ingest pipeline.

The response reports what happened to each extracted fact, which is the only way
to see a SUPERSEDE without querying the database directly.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends

from ..deps import get_conn, get_embeddings, get_llm
from ..llm.base import LLMProvider
from ..llm.embeddings import EmbeddingClient
from ..pipeline import ingest_message
from ..schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["chat"])


def _summarize(result) -> str:
    if result.error and not result.outcomes:
        return "Recorded to the event log, but fact processing failed."
    if not result.outcomes:
        return "Recorded. No durable facts in that message."

    counts: dict[str, int] = {}
    for outcome in result.outcomes:
        counts[outcome.resolution] = counts.get(outcome.resolution, 0) + 1
    parts = [f"{n} {label.lower()}" for label, n in sorted(counts.items())]
    return "Recorded. " + ", ".join(parts) + "."


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    conn: asyncpg.Connection = Depends(get_conn),
    llm: LLMProvider = Depends(get_llm),
    embeddings: EmbeddingClient = Depends(get_embeddings),
) -> ChatResponse:
    result = await ingest_message(
        conn,
        llm=llm,
        embeddings=embeddings,
        user_id=payload.user_id,
        message=payload.message,
    )
    return ChatResponse(
        reply=_summarize(result),
        event_id=result.event_id,
        outcomes=result.outcomes,
        error=result.error,
    )
