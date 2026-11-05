"""### System endpoints: health and readiness."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from lexcrawl import __version__
from lexcrawl.api.dependencies import SessionDep
from lexcrawl.api.exceptions import DependencyUnavailableError
from lexcrawl.api.openapi import UNAVAILABLE_RESPONSE

router = APIRouter(tags=["System"])


@router.get("/health", summary="Liveness probe", description="Reports that the API process is running.")
def health() -> dict[str, str]:
    """### Report that the application process is running."""
    return {"status": "ok", "version": __version__}


@router.get(
    "/ready",
    summary="Readiness probe",
    description="Reports readiness by checking that PostgreSQL is reachable.",
    responses={503: UNAVAILABLE_RESPONSE},
)
def ready(session: SessionDep) -> dict[str, str]:
    """### Report readiness, checking that PostgreSQL is reachable."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        # $ A dependency outage is an expected condition, not an internal failure.$
        raise DependencyUnavailableError("database is unavailable") from error
    return {"status": "ready", "database": "ok"}
