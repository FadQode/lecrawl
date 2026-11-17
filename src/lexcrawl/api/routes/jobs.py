"""### Job submission endpoint (authenticated, idempotent, async)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.api.auth import require_bearer_token
from lexcrawl.api.contracts import JobAccepted, JobRequest
from lexcrawl.api.dependencies import get_factory, get_settings_dep
from lexcrawl.config import Settings
from lexcrawl.db.session import session_scope
from lexcrawl.factory import SOURCE_NAMES
from lexcrawl.repositories import jobs as jobs_repo

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "",
    status_code=202,
    response_model=JobAccepted,
    dependencies=[Depends(require_bearer_token)],
)
def submit_job(
    payload: JobRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    factory: sessionmaker[Session] = Depends(get_factory),
    settings: Settings = Depends(get_settings_dep),
) -> JobAccepted:
    """### Enqueue a crawl job and return 202 Accepted with its run id."""
    if idempotency_key is not None and len(idempotency_key) > 128:
        raise HTTPException(status_code=422, detail="Idempotency-Key must be at most 128 characters")
    if payload.sources is not None:
        invalid = sorted(set(payload.sources) - set(SOURCE_NAMES))
        if invalid:
            raise HTTPException(status_code=422, detail=f"unknown sources: {invalid}")
    with session_scope(factory) as session:
        # $ A retry with an existing idempotency key must return the original run_id, even if that
        # $ job is now claimed/running, so resolve the key before the active-job conflict check.
        existing = jobs_repo.get_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            requested = {
                "sources": sorted(payload.sources) if payload.sources else None,
                "trigger": payload.trigger,
            }
            if existing.payload != requested:
                raise HTTPException(
                    status_code=409, detail="Idempotency-Key was reused with a different request"
                )
            return JobAccepted(
                run_id=existing.run_id, status=existing.status, status_url=f"/runs/{existing.run_id}"
            )
        if jobs_repo.has_active(
            session,
            now=datetime.now(tz=UTC),
            stale_after_seconds=settings.job_claim_lease_seconds,
        ):
            raise HTTPException(status_code=409, detail="a crawl run is already active")
        try:
            result = jobs_repo.enqueue(
                session,
                trigger_type=payload.trigger,
                now=datetime.now(tz=UTC),
                idempotency_key=idempotency_key,
                sources=payload.sources,
            )
        except jobs_repo.JobConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
    return JobAccepted(run_id=result.run_id, status="queued", status_url=f"/runs/{result.run_id}")
