"""### Process and database health endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Engine, text

from lexcrawl.api.dependencies import get_engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """### Report that the process is up."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(engine: Engine | None = Depends(get_engine)) -> dict[str, str]:
    """### Report database readiness by executing a trivial query."""
    if engine is None:
        raise HTTPException(status_code=503, detail="database is not configured")
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as error:
        raise HTTPException(status_code=503, detail="database is not ready") from error
    return {"status": "ready"}
