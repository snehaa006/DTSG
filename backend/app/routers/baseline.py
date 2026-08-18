"""Phase 7 — naive RAG baseline, for measuring what DTSG actually buys.

Naive retrieval ranks purely by cosine similarity over everything ever stored.
It has no notion of a fact being superseded, so a fact that stopped being true
still competes on equal terms with the one that replaced it.

**On fairness.** Both endpoints read the same `memories` rows, which might look
like DTSG is being given a curated corpus. It is not, and the equivalence is
worth stating precisely: a naive store never expires anything, so its corpus is
"every fact ever extracted". DTSG never deletes anything either — it only marks
rows EXPIRED. So the row sets are identical; the baseline simply ignores the
status and time columns, exactly as a system that never wrote them would. The
comparison isolates the retrieval policy, which is the thing under test.

The one difference to keep in mind: DTSG's REINFORCE collapses a restatement
into a confidence bump rather than a second row, so a naive store would hold a
few more near-duplicates than this baseline sees. That works against DTSG in the
comparison (duplicates make naive retrieval look more consistent), so the
measurement is conservative rather than flattering.
"""

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
from ..schemas import (
    BaselineResponse,
    BaselineResult,
    CompareResponse,
    RetrieveRequest,
)
from ..store import memories as memories_store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/baseline", tags=["baseline"])


async def _embed(embeddings: EmbeddingClient, query: str) -> list[float]:
    try:
        embedding = await embeddings.embed_query(query)
    except Exception as exc:  # noqa: BLE001
        log.warning("query embedding failed", exc_info=True)
        raise HTTPException(status_code=502, detail="embedding service unavailable") from exc
    return embedding


@router.post("/retrieve", response_model=BaselineResponse)
async def baseline_retrieve(
    payload: RetrieveRequest,
    conn: asyncpg.Connection = Depends(get_conn),
    embeddings: EmbeddingClient = Depends(get_embeddings),
) -> BaselineResponse:
    """Top-K by cosine similarity. No status weighting, no decay, no re-ranking."""
    embedding = await _embed(embeddings, payload.query)
    rows = await memories_store.search_naive(
        conn, user_id=payload.user_id, embedding=embedding, limit=payload.limit
    )
    return BaselineResponse(
        results=[
            BaselineResult(**memory.model_dump(), similarity=similarity)
            for memory, similarity in rows
        ]
    )


@router.post("/compare", response_model=CompareResponse)
async def compare(
    payload: RetrieveRequest,
    conn: asyncpg.Connection = Depends(get_conn),
    embeddings: EmbeddingClient = Depends(get_embeddings),
) -> CompareResponse:
    """Run both retrievals on one query and report where they diverge.

    One embedding call serves both, so the two rankings differ only in policy —
    not in an incidental difference between two vectors of the same text.
    """
    embedding = await _embed(embeddings, payload.query)
    evaluated_at = payload.as_of or datetime.now(timezone.utc)
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)

    naive_rows = await memories_store.search_naive(
        conn, user_id=payload.user_id, embedding=embedding, limit=payload.limit
    )
    baseline = [
        BaselineResult(**memory.model_dump(), similarity=similarity)
        for memory, similarity in naive_rows
    ]

    pool = await memories_store.search_candidates(
        conn,
        user_id=payload.user_id,
        embedding=embedding,
        pool_size=candidate_pool_size(payload.limit),
        mode=payload.mode,
        as_of=payload.as_of,
    )
    dtsg = rerank(
        pool,
        at=evaluated_at,
        limit=payload.limit,
        lambda_per_day=(
            payload.lambda_per_day
            if payload.lambda_per_day is not None
            else DEFAULT_LAMBDA_PER_DAY
        ),
        expired_weight=(
            payload.expired_weight
            if payload.expired_weight is not None
            else DEFAULT_EXPIRED_WEIGHT
        ),
    )

    dtsg_ids = {r.id for r in dtsg}
    baseline_ids = {r.id for r in baseline}
    union = dtsg_ids | baseline_ids
    # Jaccard rather than |intersection| / k, so a query that returns fewer than
    # k rows on either side is not scored as a miss for the rows it never had.
    overlap = len(dtsg_ids & baseline_ids) / len(union) if union else 1.0

    return CompareResponse(
        query=payload.query,
        dtsg=dtsg,
        baseline=baseline,
        overlap=overlap,
        baseline_top_is_stale=bool(baseline) and baseline[0].status == "EXPIRED",
        dtsg_top_is_stale=bool(dtsg) and dtsg[0].status == "EXPIRED",
    )
