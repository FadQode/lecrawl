"""### Durable job queue operations (enqueue, claim, status)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import bindparam, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lexcrawl.db.models import CrawlRun, Job
from lexcrawl.locking import advisory_lock_key_for

ACTIVE_JOB_STATUSES = ("claimed", "running")


class JobConflictError(RuntimeError):
    """### Raised when a job for the same source is already active."""


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
    source: str | None = None
    sources: list[str] | None = None


def enqueue(
    session: Session,
    *,
    trigger_type: str,
    now: datetime,
    idempotency_key: str | None = None,
    source: str | None = None,
    sources: Sequence[str] | None = None,
) -> JobEnqueue:
    """### Enqueue a per-source job, reusing an existing one for the same idempotency key."""
    run_uuid = uuid.uuid4()
    resolved_source = source or (sources[0] if sources and len(sources) == 1 else None)
    # $ The stored signature lets the API detect a key reused with a different request.$
    payload: dict[str, object] = {
        "sources": [resolved_source] if resolved_source is not None else (sorted(sources) if sources else None),
        "trigger": trigger_type,
    }
    statement = (
        pg_insert(Job)
        .values(
            run_id=run_uuid,
            trigger_type=trigger_type,
            source=resolved_source,
            status="queued",
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
            payload=payload,
        )
        .on_conflict_do_nothing(index_elements=[Job.idempotency_key, Job.source])
        .returning(Job.id, Job.run_id)
    )
    try:
        # $ A SAVEPOINT keeps an active-per-source conflict from aborting the caller's transaction.$
        with session.begin_nested():
            row = session.execute(statement).first()
    except IntegrityError as error:
        raise JobConflictError("another crawl job is already active for this source") from error
    if row is not None:
        return JobEnqueue(id=row.id, run_id=str(row.run_id), created=True, payload=payload)
    if idempotency_key is None:
        raise RuntimeError("job enqueue failed without an existing idempotency key")
    existing = session.scalar(
        select(Job).where(Job.idempotency_key == idempotency_key, Job.source.is_(resolved_source))
        if resolved_source is None
        else select(Job).where(Job.idempotency_key == idempotency_key, Job.source == resolved_source)
    )
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


def get_by_idempotency_key(session: Session, key: str | None, *, source: str | None = None) -> JobEnqueue | None:
    """### Return the job already enqueued for an idempotency key and source, if any."""
    if key is None:
        return None
    condition = Job.source.is_(None) if source is None else Job.source == source
    row = session.scalar(select(Job).where(Job.idempotency_key == key, condition))
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
    return JobClaim(
        id=row.id,
        run_id=str(row.run_id),
        trigger_type=row.trigger_type,
        source=row.source,
        sources=sources,
    )


def mark_status(
    session: Session, *, job_id: int, status: str, now: datetime, result_status: str | None = None
) -> None:
    """### Set a job's status (and optionally the run's own result status)."""
    row = session.get(Job, job_id)
    if row is None:
        return
    row.status = status
    if result_status is not None:
        row.result_status = result_status
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


_FRESH_ACTIVE_JOB_SQL = (
    select(Job.id)
    .where(Job.status.in_(ACTIVE_JOB_STATUSES), Job.claimed_at >= bindparam("cutoff"))
    .limit(1)
)

# $ A session-level advisory lock for key K appears as classid/objid of K's 32-bit halves.$
_ADVISORY_HELD_SQL = text(
    "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
    "AND objsubid = 1 AND classid = :classid AND objid = :objid)"
)


def _lock_key_parts(key: int) -> tuple[int, int]:
    """### Split a signed 64-bit advisory key into its unsigned (classid, objid) halves."""
    unsigned = key & 0xFFFFFFFFFFFFFFFF
    return (unsigned >> 32) & 0xFFFFFFFF, unsigned & 0xFFFFFFFF


def advisory_lock_held(session: Session, key: int) -> bool:
    """### Report whether a specific session-level advisory lock is currently held."""
    classid, objid = _lock_key_parts(key)
    return bool(session.scalar(_ADVISORY_HELD_SQL, {"classid": classid, "objid": objid}))


def any_source_lock_held(session: Session, source_names: Sequence[str]) -> bool:
    """### Report whether any known source's advisory lock is held (a crawl is live)."""
    return any(advisory_lock_held(session, advisory_lock_key_for(name)) for name in source_names)


def _has_fresh_active_job(session: Session, *, now: datetime, stale_after_seconds: int) -> bool:
    """### Report whether a worker holds a fresh claim on any source."""
    cutoff = now - timedelta(seconds=max(1, stale_after_seconds))
    return session.scalar(_FRESH_ACTIVE_JOB_SQL, {"cutoff": cutoff}) is not None


def has_active(
    session: Session, *, now: datetime, stale_after_seconds: int, source_names: Sequence[str] = ()
) -> bool:
    """### Report whether a crawl is live: an advisory lock is held or a claim is fresh."""
    if source_names and any_source_lock_held(session, source_names):
        return True
    return _has_fresh_active_job(session, now=now, stale_after_seconds=stale_after_seconds)


def abandon_stale_runs(
    session: Session,
    *,
    now: datetime,
    source_names: Sequence[str] = (),
    stale_after_seconds: int = 900,
) -> int:
    """### Mark orphaned 'running' runs abandoned, but only when no crawl is live.

    A run started by the CLI, the scheduler, or GitHub Actions has no job row, so a
    fresh-claim check alone would abandon it within seconds. The per-source advisory
    locks (held for the whole run) are the authoritative liveness signal.
    """
    if source_names and any_source_lock_held(session, source_names):
        return 0
    if _has_fresh_active_job(session, now=now, stale_after_seconds=stale_after_seconds):
        return 0
    result = session.execute(
        update(CrawlRun).where(CrawlRun.status == "running").values(status="abandoned", finished_at=now)
    )
    return int(getattr(result, "rowcount", 0) or 0)


def touch(session: Session, *, job_id: int, now: datetime) -> None:
    """### Refresh a running job's claim heartbeat."""
    row = session.get(Job, job_id)
    if row is not None:
        row.claimed_at = now
        row.updated_at = now


def count_by_status(session: Session) -> dict[str, int]:
    """### Count jobs grouped by status (used for queue-depth metrics)."""
    rows = session.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    return {str(status): int(count) for status, count in rows}
