"""### PostgreSQL tests for the worker lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import delete

from lexcrawl import worker
from lexcrawl.config import get_settings
from lexcrawl.db.models import Job
from lexcrawl.db.session import session_scope
from lexcrawl.repositories.jobs import enqueue


def _clear(factory) -> None:  # type: ignore[no-untyped-def]
    with session_scope(factory) as session:
        session.execute(delete(Job))


@pytest.mark.db
def test_worker_completes_and_records_result_status(pg_factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """### The worker claims a job, runs it with its run_id, and records result_status."""
    settings = get_settings()
    now = datetime.now(tz=UTC)
    captured: dict[str, object] = {}

    def fake(settings, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return [SimpleNamespace(status="success"), SimpleNamespace(status="partial_failure")]

    monkeypatch.setattr(worker, "run_daily", fake)
    _clear(pg_factory)
    try:
        with session_scope(pg_factory) as session:
            job = enqueue(session, trigger_type="manual", now=now, idempotency_key="wtest")
        assert worker.run_once(settings) is True
        assert captured.get("run_id") == job.run_id
        with session_scope(pg_factory) as session:
            row = session.get(Job, job.id)
            assert row is not None
            assert row.status == "succeeded" and row.result_status == "partial_failure"
    finally:
        _clear(pg_factory)


@pytest.mark.db
def test_worker_marks_failed_on_exception(pg_factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """### An exception during the crawl marks the job failed."""
    settings = get_settings()
    now = datetime.now(tz=UTC)

    def boom(settings, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("crawl exploded")

    monkeypatch.setattr(worker, "run_daily", boom)
    _clear(pg_factory)
    try:
        with session_scope(pg_factory) as session:
            job = enqueue(session, trigger_type="manual", now=now, idempotency_key="wfail")
        assert worker.run_once(settings) is True
        with session_scope(pg_factory) as session:
            row = session.get(Job, job.id)
            assert row is not None and row.status == "failed" and row.result_status == "failed"
    finally:
        _clear(pg_factory)
