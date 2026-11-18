"""### Job submission endpoint (authenticated, idempotent, one job per source)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.api.auth import require_bearer_token
from lexcrawl.api.contracts import JobAccepted, JobRefModel, JobRequest
from lexcrawl.api.dependencies import get_factory
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
) -> JobAccepted:
    """### Enqueue one job per source and return 202 Accepted with their run ids."""
    if idempotency_key is not None and len(idempotency_key) > 128:
        raise HTTPException(status_code=422, detail="Idempotency-Key must be at most 128 characters")
    requested = tuple(payload.sources) if payload.sources else SOURCE_NAMES
    invalid = sorted(set(requested) - set(SOURCE_NAMES))
    if invalid:
        raise HTTPException(status_code=422, detail=f"unknown sources: {invalid}")

    now = datetime.now(tz=UTC)
    with session_scope(factory) as session:
        jobs: list[JobRefModel] = []
        for source in requested:
            # $ Per-source idempotency: the same key plus source resolves to the same job.$
            existing = jobs_repo.get_by_idempotency_key(session, idempotency_key, source=source)
            if existing is not None:
                if existing.payload != {"sources": [source], "trigger": payload.trigger}:
                    raise HTTPException(
                        status_code=409, detail="Idempotency-Key was reused with a different request"
                    )
                jobs.append(_job_ref(source, existing.run_id, existing.status))
                continue
            try:
                result = jobs_repo.enqueue(
                    session,
                    trigger_type=payload.trigger,
                    now=now,
                    idempotency_key=idempotency_key,
                    source=source,
                    sources=[source],
                )
            except jobs_repo.JobConflictError as error:
                raise HTTPException(
                    status_code=409, detail=f"a crawl job is already active for source {source!r}"
                ) from error
            jobs.append(_job_ref(source, result.run_id, "queued"))
    return JobAccepted(jobs=jobs)


def _job_ref(source: str, run_id: str, status: str) -> JobRefModel:
    """### Build the accepted-job reference for one source."""
    return JobRefModel(source=source, run_id=run_id, status=status, status_url=f"/runs/{run_id}")
