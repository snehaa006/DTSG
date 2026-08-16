"""Chat entry point.

Phase 1 deliberately does not write anything: it proves the browser -> Render ->
Supabase path is wired end to end. The immutable event-log insert lands here in
Phase 3, extraction in Phase 2, and the conflict-resolving write pipeline in
Phase 4.
"""

from fastapi import APIRouter

from ..schemas import ChatRequest, ChatResponse

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    return ChatResponse(
        reply=(
            f"Received {len(payload.message)} characters. "
            "Phase 1 is transport only — no event is written and no fact is "
            "extracted yet."
        ),
        event_id=None,
    )
