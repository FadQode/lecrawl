"""### Worker: claim and execute queued crawl jobs."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.checks.runner import run_daily
from lexcrawl.config import Settings
from lexcrawl.db.engine import create_db_engine, database_configured
from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.factory import CHECK_SOURCES
from lexcrawl.locking import RunInProgressError
from lexcrawl.logging import get_logger
from lexcrawl.repositories.jobs import abandon_stale_runs, claim_next, mark_status, reclaim_stale, touch

LOGGER = get_logger(__name__)

DEFAULT_POLL_SECONDS = 5.0


def run_once(settings: Settings, *, worker_name: str | None = None, engine: Engine | None = None) -> bool:
    """### Claim and execute one queued job; return False when nothing was queued.

    When an engine is injected it is reused as-is; otherwise a short-lived engine is
    created for this single call (the `--once` path and tests).
    """
    if engine is not None:
        return _process_once(settings, engine, create_session_factory(engine), worker_name)
    if not database_configured(settings):
        raise ValueError("DATABASE_URL is required for the worker")
    worker_engine = create_db_engine(settings)
    try:
        return _process_once(settings, worker_engine, create_session_factory(worker_engine), worker_name)
    finally:
        worker_engine.dispose()


def run_forever(
    settings: Settings,
    *,
    interval_seconds: float = DEFAULT_POLL_SECONDS,
    worker_name: str | None = None,
    stop: threading.Event | None = None,
) -> None:
    """### Poll for queued jobs until stopped, reusing one engine for the whole process."""
    if not database_configured(settings):
        raise ValueError("DATABASE_URL is required for the worker")
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    try:
        while stop is None or not stop.is_set():
            if not _process_once(settings, engine, factory, worker_name):
                _pause(stop, interval_seconds)
    finally:
        engine.dispose()


def _pause(stop: threading.Event | None, seconds: float) -> None:
    """### Wait between polls, waking early when the stop flag is set."""
    if stop is not None:
        stop.wait(seconds)
    else:
        time.sleep(seconds)


def _process_once(
    settings: Settings,
    engine: Engine,
    factory: sessionmaker[Session],
    worker_name: str | None,
) -> bool:
    """### Claim and run one job over an already-built engine/session factory."""
    worker = worker_name or os.environ.get("HOSTNAME", "worker")
    lease = settings.job_claim_lease_seconds
    now = datetime.now(tz=UTC)
    with session_scope(factory) as session:
        reclaim_stale(session, now=now, lease_seconds=lease)
        abandon_stale_runs(session, now=now, source_names=CHECK_SOURCES, stale_after_seconds=lease)
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
    # $ Job status means "ran to completion"; result_status records the run's quality.$
    status = "succeeded"
    result_status: str | None = None
    try:
        results = run_daily(
            settings,
            sources=sources,
            trigger=claim.trigger_type,
            run_id=claim.run_id,
            engine=engine,
        )
        result_status = _aggregate_result_status(results)
    except RunInProgressError:
        LOGGER.warning("job_requeued_lock_held", extra={"job_id": claim.id})
        with session_scope(factory) as session:
            mark_status(session, job_id=claim.id, status="queued", now=datetime.now(tz=UTC))
        return False
    except Exception:
        LOGGER.exception("job_failed", extra={"job_id": claim.id, "run_id": claim.run_id})
        status = "failed"
        result_status = "failed"
    finally:
        stop.set()
        beat.join(timeout=5)

    with session_scope(factory) as session:
        mark_status(
            session, job_id=claim.id, status=status, result_status=result_status, now=datetime.now(tz=UTC)
        )
    LOGGER.info(
        "job_finished",
        extra={"job_id": claim.id, "run_id": claim.run_id, "status": status, "result_status": result_status},
    )
    return True


def _aggregate_result_status(results: Sequence[object]) -> str:
    """### Aggregate per-source statuses into success/partial_failure/failed."""
    statuses = {getattr(result, "status", "failed") for result in results}
    if not statuses or statuses == {"success"}:
        return "success"
    if statuses == {"failed"}:
        return "failed"
    return "partial_failure"
