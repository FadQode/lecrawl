"""### Worker: claim and execute queued crawl jobs."""

from __future__ import annotations

import os
from datetime import UTC, datetime

from lexcrawl.checks.runner import run_daily
from lexcrawl.config import Settings
from lexcrawl.db.engine import create_db_engine
from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.factory import CHECK_SOURCES
from lexcrawl.locking import RunInProgressError
from lexcrawl.logging import get_logger
from lexcrawl.repositories.jobs import claim_next, mark_status, reclaim_stale

LOGGER = get_logger(__name__)


def run_once(settings: Settings, *, worker_name: str | None = None) -> bool:
    """### Claim and execute one queued job; return False when nothing was queued."""
    if settings.state_backend == "json":
        raise ValueError("the worker requires STATE_BACKEND=db")
    if not settings.database_url.strip():
        raise ValueError("DATABASE_URL is required for the worker")
    worker = worker_name or os.environ.get("HOSTNAME", "worker")
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    now = datetime.now(tz=UTC)
    try:
        with session_scope(factory) as session:
            reclaim_stale(session, now=now, lease_seconds=900)
        with session_scope(factory) as session:
            claim = claim_next(session, worker=worker, now=now)
        if claim is None:
            return False
        with session_scope(factory) as session:
            mark_status(session, job_id=claim.id, status="running", now=datetime.now(tz=UTC))
        sources = tuple(claim.sources) if claim.sources else CHECK_SOURCES
        try:
            run_daily(
                settings,
                sources=sources,
                trigger=claim.trigger_type,
                run_id=claim.run_id,
                engine=engine,
            )
        except RunInProgressError:
            LOGGER.warning("job_requeued_lock_held", extra={"job_id": claim.id})
            with session_scope(factory) as session:
                mark_status(session, job_id=claim.id, status="queued", now=datetime.now(tz=UTC))
            return False
        except Exception:
            LOGGER.exception("job_failed", extra={"job_id": claim.id, "run_id": claim.run_id})
            with session_scope(factory) as session:
                mark_status(session, job_id=claim.id, status="failed", now=datetime.now(tz=UTC))
            return True
        with session_scope(factory) as session:
            mark_status(session, job_id=claim.id, status="succeeded", now=datetime.now(tz=UTC))
        LOGGER.info("job_succeeded", extra={"job_id": claim.id, "run_id": claim.run_id})
        return True
    finally:
        engine.dispose()
