"""### PostgreSQL tests for the durable job queue."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete

from lexcrawl.db.models import CrawlRun, Job
from lexcrawl.db.session import session_scope
from lexcrawl.locking import AdvisoryLock, advisory_lock_key_for
from lexcrawl.repositories.crawl_runs import start_run
from lexcrawl.repositories.jobs import (
    JobConflictError,
    abandon_stale_runs,
    claim_next,
    enqueue,
    mark_status,
    reclaim_stale,
)


def _clear(factory) -> None:  # type: ignore[no-untyped-def]
    with session_scope(factory) as session:
        session.execute(delete(Job))


@pytest.mark.db
def test_idempotency_and_per_source_active(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### Same key/source returns the same job; a second active job per source conflicts."""
    now = datetime.now(tz=UTC)
    _clear(pg_factory)
    try:
        with session_scope(pg_factory) as session:
            first = enqueue(session, trigger_type="manual", now=now, idempotency_key="k1", sources=["uae"])
            again = enqueue(session, trigger_type="manual", now=now, idempotency_key="k1", sources=["uae"])
            assert first.created and not again.created and again.run_id == first.run_id
            with pytest.raises(JobConflictError):
                enqueue(session, trigger_type="manual", now=now, idempotency_key="k2", sources=["uae"])
            # $ A different source is independent and may run in parallel.$
            other = enqueue(session, trigger_type="manual", now=now, idempotency_key="k3", sources=["cba"])
            assert other.created
        with session_scope(pg_factory) as session:
            claim = claim_next(session, worker="w", now=now)
            assert claim is not None and claim.source in {"uae", "cba"}
            assert claim_next(session, worker="w", now=now) is not None
            mark_status(session, job_id=claim.id, status="succeeded", now=now)
    finally:
        _clear(pg_factory)


@pytest.mark.db
def test_same_key_different_payload_conflicts(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### The enqueue conflict path rejects a same-key/same-source/different-trigger reuse."""
    now = datetime.now(tz=UTC)
    _clear(pg_factory)
    try:
        with session_scope(pg_factory) as session:
            enqueue(session, trigger_type="manual", now=now, idempotency_key="sig", source="uae")
        with session_scope(pg_factory) as session, pytest.raises(JobConflictError):
            enqueue(session, trigger_type="api", now=now, idempotency_key="sig", source="uae")
    finally:
        _clear(pg_factory)


@pytest.mark.db
def test_reclaim_stale_claim(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### A stale claim is requeued and reclaimable."""
    now = datetime.now(tz=UTC)
    _clear(pg_factory)
    try:
        with session_scope(pg_factory) as session:
            job = enqueue(session, trigger_type="manual", now=now, idempotency_key="stale", sources=["uae"])
            claimed = claim_next(session, worker="w1", now=now)
            assert claimed is not None
            row = session.get(Job, claimed.id)
            assert row is not None
            row.claimed_at = now - timedelta(hours=1)
        with session_scope(pg_factory) as session:
            assert reclaim_stale(session, now=now, lease_seconds=60) >= 1
        with session_scope(pg_factory) as session:
            again = claim_next(session, worker="w2", now=now)
            assert again is not None and again.run_id == job.run_id
    finally:
        _clear(pg_factory)


@pytest.mark.db
def test_abandon_respects_source_lock_for_jobless_run(pg_engine, pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### A running run with no job row is not abandoned while its source lock is held.

    CLI, scheduler, and GitHub Actions runs have no job row; the per-source advisory
    lock is the liveness signal that keeps them from being abandoned mid-run.
    """
    now = datetime.now(tz=UTC)
    run_id: int | None = None
    lock = AdvisoryLock(pg_engine, key=advisory_lock_key_for("uae"))
    _clear(pg_factory)
    try:
        with session_scope(pg_factory) as session:
            run_id, _ = start_run(session, trigger_type="manual", now=now)
        assert lock.acquire() is True
        with session_scope(pg_factory) as session:
            assert abandon_stale_runs(session, now=now, source_names=["uae"]) == 0
        lock.release()
        with session_scope(pg_factory) as session:
            assert abandon_stale_runs(session, now=now, source_names=["uae"]) >= 1
        with session_scope(pg_factory) as session:
            run = session.get(CrawlRun, run_id)
            assert run is not None and run.status == "abandoned"
    finally:
        lock.release()
        with session_scope(pg_factory) as session:
            if run_id is not None:
                session.execute(delete(CrawlRun).where(CrawlRun.id == run_id))
        _clear(pg_factory)


@pytest.mark.db
def test_abandon_only_when_no_fresh_claim(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### Running runs are abandoned only when no worker holds a fresh claim."""
    now = datetime.now(tz=UTC)
    run_id: int | None = None
    _clear(pg_factory)
    try:
        with session_scope(pg_factory) as session:
            run_id, _ = start_run(session, trigger_type="manual", now=now)
            enqueue(session, trigger_type="manual", now=now, idempotency_key="abandon", sources=["uae"])
        with session_scope(pg_factory) as session:
            claim = claim_next(session, worker="w", now=now)
            assert claim is not None
        with session_scope(pg_factory) as session:
            assert abandon_stale_runs(session, now=now, stale_after_seconds=900) == 0
        with session_scope(pg_factory) as session:
            mark_status(session, job_id=claim.id, status="succeeded", now=now)
        with session_scope(pg_factory) as session:
            assert abandon_stale_runs(session, now=now, stale_after_seconds=900) >= 1
        with session_scope(pg_factory) as session:
            run = session.get(CrawlRun, run_id)
            assert run is not None and run.status == "abandoned"
    finally:
        with session_scope(pg_factory) as session:
            if run_id is not None:
                session.execute(delete(CrawlRun).where(CrawlRun.id == run_id))
        _clear(pg_factory)
