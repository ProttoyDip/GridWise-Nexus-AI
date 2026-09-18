"""GET /health — readiness endpoint.

Must return {"status": "ok"} with HTTP 200 once the service is ready
to accept judge traffic, per the Problem Statement Section 06.
"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def get_health() -> dict[str, str]:
    return {"status": "ok"}
