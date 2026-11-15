"""Health-check router."""

from fastapi import APIRouter

router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}
