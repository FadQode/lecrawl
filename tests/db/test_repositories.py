"""### PostgreSQL tests for document/resource repositories and accounting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from lexcrawl.db.models import CrawlRun, Document, Source
from lexcrawl.db.session import session_scope
from lexcrawl.models.document import CanonicalDocument, DocumentResource, ensure_text_marker
from lexcrawl.repositories.crawl_runs import finish_run, record_source, start_run
from lexcrawl.repositories.documents import upsert_document
from lexcrawl.repositories.resources import claim_due_resources
from lexcrawl.repositories.sources import ensure_source, source_id_for
from lexcrawl.services.content_processing import ContentProcessingService
from lexcrawl.services.credit_accounting import CreditAccountingService

TEST_SOURCE = "test_repo_source"


def _document(*, url: str, text: str | None, status: str) -> CanonicalDocument:
    """### Build a canonical document with an optional body."""
    body = f"{ensure_text_marker('x')}" if text else None
    from lexcrawl.models.document import compute_content_hash

    return CanonicalDocument(
        source=TEST_SOURCE,
        source_url=url,
        title="T",
        language="en",
        text=body,
        content_hash=compute_content_hash(body) if body else None,
        extraction_status=status,  # type: ignore[arg-type]
    )


def _cleanup(factory) -> None:  # type: ignore[no-untyped-def]
    with session_scope(factory) as session:
        source_id = source_id_for(session, TEST_SOURCE)
        if source_id is not None:
            session.execute(delete(Document).where(Document.source_id == source_id))
        session.execute(delete(Source).where(Source.code == TEST_SOURCE))


@pytest.mark.db
def test_monotonic_upsert_preserves_content(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### A failed re-fetch does not overwrite good content."""
    now = datetime.now(tz=UTC)
    _cleanup(pg_factory)
    try:
        with session_scope(pg_factory) as session:
            source_id = ensure_source(session, TEST_SOURCE, "Repo source")
            upsert_document(session, source_id=source_id, document=_document(url="https://t.test/a", text="x", status="complete"), now=now)
            upsert_document(session, source_id=source_id, document=_document(url="https://t.test/a", text=None, status="failed"), now=now)
        with session_scope(pg_factory) as session:
            source_id = source_id_for(session, TEST_SOURCE)
            assert source_id is not None
            row = session.scalars(select(Document).where(Document.source_id == source_id)).one()
            assert row.extraction_status == "complete" and row.text is not None
            assert row.extraction_attempts == 1
    finally:
        _cleanup(pg_factory)


@pytest.mark.db
def test_claim_lease_expiry(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### A claim blocks immediate re-claim and frees after the lease."""
    now = datetime.now(tz=UTC)
    _cleanup(pg_factory)
    try:
        with session_scope(pg_factory) as session:
            ensure_source(session, TEST_SOURCE, "Repo source")
        service = ContentProcessingService(pg_factory)
        pending = DocumentResource(title="pdf", url="https://t.test/a.pdf", kind="pdf", status="pending")
        service.persist(TEST_SOURCE, [_document(url="https://t.test/a", text="x", status="complete").model_copy(update={"resources": (pending,)})])
        with session_scope(pg_factory) as session:
            source_id = source_id_for(session, TEST_SOURCE)
            assert source_id is not None
            assert len(claim_due_resources(session, attempt_cap=5, source_id=source_id, now=now, lease_seconds=300)) == 1
        with session_scope(pg_factory) as session:
            source_id = source_id_for(session, TEST_SOURCE)
            assert source_id is not None
            assert claim_due_resources(session, attempt_cap=5, source_id=source_id, now=now, lease_seconds=300) == []
        later = now + timedelta(seconds=301)
        with session_scope(pg_factory) as session:
            source_id = source_id_for(session, TEST_SOURCE)
            assert source_id is not None
            assert len(claim_due_resources(session, attempt_cap=5, source_id=source_id, now=later, lease_seconds=300)) == 1
    finally:
        _cleanup(pg_factory)


@pytest.mark.db
def test_credit_accounting_actual(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### Actual credits come from run records."""
    now = datetime.now(tz=UTC)
    period = now.strftime("%Y-%m")
    _cleanup(pg_factory)
    run_id: int | None = None
    try:
        with session_scope(pg_factory) as session:
            source_id = ensure_source(session, TEST_SOURCE, "Repo source")
            run_id, _ = start_run(session, trigger_type="manual", now=now)
            record_source(
                session, crawl_run_id=run_id, source_id=source_id, check_outcome="new_data", status="success",
                discovered=0, inserted=0, updated=0, skipped=0, failed=0, resources=0, pending=0,
                credits=4, pagination_complete=True, detail="ok", started_at=now, finished_at=now,
            )
            finish_run(session, crawl_run_id=run_id, status="success", credits=4, duration_ms=5, now=now)
        assert CreditAccountingService(pg_factory).actual(period) >= 4
    finally:
        with session_scope(pg_factory) as session:
            if run_id is not None:
                session.execute(delete(CrawlRun).where(CrawlRun.id == run_id))
        _cleanup(pg_factory)
