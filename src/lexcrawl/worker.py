"""### Worker: claim and execute queued crawl jobs."""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime

from lexcrawl.checks.runner import run_daily
from lexcrawl.config import Settings
from lexcrawl.db.engine import create_db_engine
from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.factory import CHECK_SOURCES
from lexcrawl.locking import RunInProgressError
from lexcrawl.logging import get_logger
from lexcrawl.repositories.jobs import abandon_stale_runs, claim_next, mark_status, reclaim_stale, touch

LOGGER = get_logger(__name__)


def run_once(settings: Settings, *, worker_name: str | None = None) -> bool:
    """### Claim and execute one queued job; return False when nothing was queued."""
    if settings.state_backend == "json":
        raise ValueError("the worker requires STATE_BACKEND=db")
    if not settings.database_url.strip():
        raise ValueError("DATABASE_URL is required for the worker")
    worker = worker_name or os.environ.get("HOSTNAME", "worker")
    lease = settings.job_claim_lease_seconds
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    now = datetime.now(tz=UTC)
    try:
        with session_scope(factory) as session:
            reclaim_stale(session, now=now, lease_seconds=lease)
            abandon_stale_runs(session, now=now, lease_seconds=lease)
        with session_scope(factory) as session:
            claim = claim_next(session, worker=worker, now=now)
        if claim is None:
            return False
        with session_scope(factory) as session:
            mark_status(session, job_id=claim.id, status="running", now=datetime.now(tz=UTC))

        stop = threading.Event()

        def heartbeat() -> None:
            """### Refresh the job's claim while the crawl runs (prevents false requeue)."""
            interval = max(5.0, lease / 3)
            while not stop.wait(interval):
                try:
                    with session_scope(factory) as session:
                        touch(session, job_id=claim.id, now=datetime.now(tz=UTC))
                except Exception:
                    LOGGER.warning("job_heartbeat_failed", extra={"job_id": claim.id})

        beat = threading.Thread(target=heartbeat, daemon=True)
        beat.start()

        sources = tuple(claim.sources) if claim.sources else CHECK_SOURCES
        status = "succeeded"
        try:
            results = run_daily(
                settings,
                sources=sources,
                trigger=claim.trigger_type,
                run_id=claim.run_id,
                engine=engine,
            )
            # $ A job whose every source failed must not be reported as succeeded.$
            if results and all(result.status == "failed" for result in results):
                status = "failed"
        except RunInProgressError:
            LOGGER.warning("job_requeued_lock_held", extra={"job_id": claim.id})
            with session_scope(factory) as session:
                mark_status(session, job_id=claim.id, status="queued", now=datetime.now(tz=UTC))
            return False
        except Exception:
            LOGGER.exception("job_failed", extra={"job_id": claim.id, "run_id": claim.run_id})
            status = "failed"
        finally:
            stop.set()
            beat.join(timeout=5)

        with session_scope(factory) as session:
            mark_status(session, job_id=claim.id, status=status, now=datetime.now(tz=UTC))
        LOGGER.info("job_finished", extra={"job_id": claim.id, "run_id": claim.run_id, "status": status})
        return True
    finally:
        engine.dispose()
