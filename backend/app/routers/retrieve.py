"""Phase 5 — temporal retrieval endpoint."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_conn, get_embeddings
from ..llm.embeddings import EmbeddingClient
from ..retrieval import (
    DEFAULT_EXPIRED_WEIGHT,
    DEFAULT_LAMBDA_PER_DAY,
    candidate_pool_size,
    rerank,
)
from ..schemas import RetrieveRequest, RetrieveResponse
from ..store import memories as memories_store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["retrieval"])


@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(
    payload: RetrieveRequest,
    conn: asyncpg.Connection = Depends(get_conn),
    embeddings: EmbeddingClient = Depends(get_embeddings),
) -> RetrieveResponse:
    if payload.mode == "as_of" and payload.as_of is None:
        raise HTTPException(
            status_code=422, detail="mode='as_of' requires an as_of timestamp"
        )

    # Everything is scored against one instant. For a point-in-time query that
    # is the moment being asked about, so both the status weight and the decay
    # are computed as of then rather than as of now.
    evaluated_at = payload.as_of or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None:
        # A naive as_of would raise on subtraction against an aware timestamp.
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)

    lambda_per_day = (
        payload.lambda_per_day
        if payload.lambda_per_day is not None
        else DEFAULT_LAMBDA_PER_DAY
    )
    expired_weight = (
        payload.expired_weight
        if payload.expired_weight is not None
        else DEFAULT_EXPIRED_WEIGHT
    )

    try:
        vectors = await embeddings.embed([payload.query])
    except Exception as exc:  # noqa: BLE001
        # Unlike the write path, there is no useful degraded answer here: with
        # no query vector every similarity is undefined, so ranking would be
        # arbitrary. Better to say so than to return confident nonsense.
        log.warning("query embedding failed", exc_info=True)
        raise HTTPException(status_code=502, detail="embedding service unavailable") from exc

    if not vectors:
        raise HTTPException(status_code=502, detail="embedding service returned nothing")

    pool_size = candidate_pool_size(payload.limit)
    candidates = await memories_store.search_candidates(
        conn,
        user_id=payload.user_id,
        embedding=vectors[0],
        pool_size=pool_size,
        mode=payload.mode,
        as_of=payload.as_of,
    )

    results = rerank(
        candidates,
        at=evaluated_at,
        limit=payload.limit,
        lambda_per_day=lambda_per_day,
        expired_weight=expired_weight,
    )

    return RetrieveResponse(
        results=results,
        mode=payload.mode,
        evaluated_at=evaluated_at,
        candidates_considered=len(candidates),
        lambda_per_day=lambda_per_day,
        expired_weight=expired_weight,
    )
