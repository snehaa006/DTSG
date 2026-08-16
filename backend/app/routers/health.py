from fastapi import APIRouter

from .. import db
from ..schemas import HealthResponse

router = APIRouter(tags=["health"])

PHASE = 2


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness + database reachability.

    Returns 200 even when the database is unreachable so Render's health check
    does not flap the service on a transient Supabase blip; read `database` to
    tell the difference.
    """
    try:
        reachable = await db.ping()
    except Exception:  # noqa: BLE001 - health must never raise
        reachable = False
    return HealthResponse(
        status="ok" if reachable else "degraded",
        database=reachable,
        phase=PHASE,
    )
