"""Phase 2 — fact extraction.

Read-only and stateless: it returns what *would* be written without writing
anything, which makes it usable both as the first stage of the Phase 4 pipeline
and as a standalone way to eyeball extraction quality against your test set.
"""

from __future__ import annotations

import logging

from google.genai import errors as genai_errors
from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_llm
from ..extraction import extract_facts
from ..llm.base import ExtractionError, LLMProvider
from ..schemas import ExtractRequest, ExtractResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["extraction"])


@router.post("/extract", response_model=ExtractResponse)
async def extract(
    payload: ExtractRequest,
    llm: LLMProvider = Depends(get_llm),
) -> ExtractResponse:
    try:
        facts = await extract_facts(llm, payload.text)
    except ExtractionError as exc:
        log.warning("extraction produced no structured output: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except genai_errors.APIError as exc:
        # Surface upstream rate limits and overloads as themselves so the caller
        # can back off, rather than flattening everything into a 500.
        status = 429 if exc.code == 429 else 502
        log.warning("gemini error %s during extraction", exc.code)
        raise HTTPException(status_code=status, detail="upstream model error") from exc

    return ExtractResponse(facts=facts, count=len(facts))
