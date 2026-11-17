"""### Durable job queue operations (enqueue, claim, status)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lexcrawl.db.models import CrawlRun, Job

ACTIVE_JOB_STATUSES = ("claimed", "running")


class JobConflictError(RuntimeError):
    """### Raised when another job is already active (single-run invariant)."""


@dataclass(frozen=True, slots=True)
class JobEnqueue:
    """### Outcome of enqueueing a job."""

    id: int
    run_id: str
    created: bool
    status: str = "queued"
    payload: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class JobClaim:
    """### A claimed job ready for execution."""

    id: int
    run_id: str
    trigger_type: str
    sources: list[str] | None = None


def enqueue(
    session: Session,
    *,
    trigger_type: str,
    now: datetime,
    idempotency_key: str | None = None,
    sources: Sequence[str] | None = None,
) -> JobEnqueue:
    """### Enqueue a job, reusing an existing one for the same idempotency key."""
    run_uuid = uuid.uuid4()
    # $ The stored signature lets the API detect a key reused with a different request.$
    payload: dict[str, object] = {
        "sources": sorted(sources) if sources else None,
        "trigger": trigger_type,
    }
    statement = (
        pg_insert(Job)
        .values(
            run_id=run_uuid,
            trigger_type=trigger_type,
            status="queued",
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
            payload=payload,
        )
        .on_conflict_do_nothing(index_elements=[Job.idempotency_key])
        .returning(Job.id, Job.run_id)
    )
    try:
        row = session.execute(statement).first()
    except IntegrityError as error:
        # $ A conflict on the single-active partial unique index means a run is already queued.$
        session.rollback()
        raise JobConflictError("another crawl job is already active") from error
    if row is not None:
        return JobEnqueue(id=row.id, run_id=str(row.run_id), created=True, payload=payload)
    if idempotency_key is None:
        raise RuntimeError("job enqueue failed without an existing idempotency key")
    existing = session.scalar(select(Job).where(Job.idempotency_key == idempotency_key))
    if existing is None:
        raise RuntimeError("job enqueue conflicted but no existing job was found")
    # $ Cover the race where two same-key/different-body requests both passed the route lookup.$
    if existing.payload != payload:
        raise JobConflictError("Idempotency-Key was reused with a different request")
    return JobEnqueue(
        id=existing.id,
        run_id=str(existing.run_id),
        created=False,
        status=existing.status,
        payload=existing.payload,
    )


def get_by_idempotency_key(session: Session, key: str | None) -> JobEnqueue | None:
    """### Return the job already enqueued for an idempotency key, if any."""
    if key is None:
        return None
    row = session.scalar(select(Job).where(Job.idempotency_key == key))
    if row is None:
        return None
    return JobEnqueue(
        id=row.id, run_id=str(row.run_id), created=False, status=row.status, payload=row.payload
    )


def claim_next(session: Session, *, worker: str, now: datetime) -> JobClaim | None:
    """### Atomically claim the oldest queued job (SKIP LOCKED)."""
    row = session.scalars(
        select(Job)
        .where(Job.status == "queued")
        .order_by(Job.created_at, Job.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    ).first()
    if row is None:
        return None
    row.status = "claimed"
    row.claimed_by = worker
    row.claimed_at = now
    row.updated_at = now
    session.flush()
    raw_sources = row.payload.get("sources") if isinstance(row.payload, dict) else None
    sources = [str(item) for item in raw_sources] if isinstance(raw_sources, list) else None
    return JobClaim(id=row.id, run_id=str(row.run_id), trigger_type=row.trigger_type, sources=sources)


def mark_status(session: Session, *, job_id: int, status: str, now: datetime) -> None:
    """### Set a job's status."""
    row = session.get(Job, job_id)
    if row is None:
        return
    row.status = status
    row.updated_at = now


def reclaim_stale(session: Session, *, now: datetime, lease_seconds: int) -> int:
    """### Requeue jobs whose worker claim lease expired (crash recovery)."""
    cutoff = now - timedelta(seconds=max(1, lease_seconds))
    result = session.execute(
        update(Job)
        .where(Job.status.in_(ACTIVE_JOB_STATUSES), Job.claimed_at < cutoff)
        .values(status="queued", updated_at=now)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def has_active(session: Session) -> bool:
    """### Report whether a job or run is currently executing."""
    active_job = session.scalar(
        select(Job.id).where(Job.status.in_(("claimed", "running"))).limit(1)
    )
    if active_job is not None:
        return True
    active_run = session.scalar(select(CrawlRun.id).where(CrawlRun.status == "running").limit(1))
    return active_run is not None
