"""### PostgreSQL tests for the content processing service and resource repository.

Covers independent attachment processing, retries up to the cap, monotonic writes,
and the TEXT-BEGIN/hash conventions. Skips cleanly without a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pymupdf
import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from lexcrawl.config import get_settings
from lexcrawl.db.engine import create_db_engine
from lexcrawl.db.models import Document, Source
from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.models.document import (
    CanonicalDocument,
    DocumentResource,
    compute_content_hash,
    ensure_text_marker,
)
from lexcrawl.repositories.resources import claim_due_resources, resources_for
from lexcrawl.repositories.sources import ensure_source, source_id_for
from lexcrawl.services.content_processing import ContentProcessingService

TEST_SOURCE = "test_content_source"


def _make_pdf(text: str) -> bytes:
    """### Build a small one-page PDF."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


class FakeFetcher:
    """### Adapts a url->bytes function to the ResourceFetcher protocol."""

    def __init__(self, fn) -> None:  # type: ignore[no-untyped-def]
        self._fn = fn

    def fetch(self, url: str, *, max_bytes: int) -> bytes:
        return self._fn(url)


class RecordingFetcher:
    """### Returns fixed bytes and records the byte limit it was given."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.max_bytes_seen: int | None = None

    def fetch(self, url: str, *, max_bytes: int) -> bytes:
        self.max_bytes_seen = max_bytes
        return self._data


def _factory_or_skip():  # type: ignore[no-untyped-def]
    """### Return an engine and session factory, skipping without PostgreSQL."""
    settings = get_settings()
    if not settings.database_url.strip():
        pytest.skip("DATABASE_URL is not configured")
    engine = create_db_engine(settings)
    try:
        with engine.connect():
            pass
    except SQLAlchemyError:
        engine.dispose()
        pytest.skip("PostgreSQL is not reachable")
    factory = create_session_factory(engine)
    with session_scope(factory) as session:
        ensure_source(session, TEST_SOURCE, "Test content source")
    return engine, factory


def _document(url: str, resources: Sequence[DocumentResource]) -> CanonicalDocument:
    """### Build a complete canonical document with attachments."""
    body = "TEXT-BEGIN\nbody"
    return CanonicalDocument(
        source=TEST_SOURCE,
        source_url=url,
        title="Doc",
        language="en",
        text=body,
        content_hash=compute_content_hash(body),
        extraction_status="complete",
        resources=tuple(resources),
    )


def _pending(url: str) -> DocumentResource:
    """### Build a pending attachment."""
    return DocumentResource(title="Att", url=url, kind="pdf", status="pending")


def _complete(url: str, text: str = "body") -> DocumentResource:
    """### Build a completed attachment."""
    marked = ensure_text_marker(text)
    return DocumentResource(
        title="Att", url=url, kind="pdf", status="complete", text=marked, content_hash=compute_content_hash(marked)
    )


def _resource_state(factory, document: CanonicalDocument):  # type: ignore[no-untyped-def]
    """### Return {url: (status, attempts, text, hash, error)} for a document's resources."""
    with session_scope(factory) as session:
        source_id = source_id_for(session, TEST_SOURCE)
        assert source_id is not None
        row = session.scalars(
            select(Document).where(
                Document.source_id == source_id, Document.identity_key == document.identity_key
            )
        ).one()
        return {
            resource.url: (
                resource.status,
                resource.attempts,
                resource.text,
                resource.content_hash,
                resource.error,
            )
            for resource in resources_for(session, document_id=row.id)
        }


def _cleanup(factory) -> None:  # type: ignore[no-untyped-def]
    """### Remove the test source, its documents, and (cascaded) resources."""
    with session_scope(factory) as session:
        source_id = source_id_for(session, TEST_SOURCE)
        if source_id is not None:
            session.execute(delete(Document).where(Document.source_id == source_id))
            session.execute(delete(Source).where(Source.code == TEST_SOURCE))


@pytest.mark.db
def test_insert_and_reprocess_are_idempotent() -> None:
    """### Reprocessing the same document and attachment creates no duplicates."""
    engine, factory = _factory_or_skip()
    service = ContentProcessingService(factory)
    document = _document("https://t.test/a", [_pending("https://t.test/a.pdf")])
    try:
        service.persist(TEST_SOURCE, [document])
        service.persist(TEST_SOURCE, [document])
        with session_scope(factory) as session:
            source_id = source_id_for(session, TEST_SOURCE)
            assert source_id is not None
            assert len(session.scalars(select(Document).where(Document.source_id == source_id)).all()) == 1
        assert len(_resource_state(factory, document)) == 1
    finally:
        _cleanup(factory)
        engine.dispose()


@pytest.mark.db
def test_attachments_process_independently() -> None:
    """### One attachment can succeed while another fails."""
    engine, factory = _factory_or_skip()

    def fetch(url: str) -> bytes:
        if url.endswith("ok.pdf"):
            return _make_pdf("Success body")
        raise RuntimeError("boom")

    service = ContentProcessingService(factory, fetcher=FakeFetcher(fetch))
    document = _document("https://t.test/b", [_pending("https://t.test/ok.pdf"), _pending("https://t.test/bad.pdf")])
    try:
        summary = service.persist(TEST_SOURCE, [document])
        assert summary.resources == 2
        assert summary.extracted == 1
        assert summary.failed == 1
        state = _resource_state(factory, document)
        ok_status, _, ok_text, ok_hash, _ = state["https://t.test/ok.pdf"]
        bad_status, bad_attempts, _, _, bad_error = state["https://t.test/bad.pdf"]
        assert ok_status == "complete"
        assert ok_text is not None and ok_text.startswith("TEXT-BEGIN")
        assert ok_hash == compute_content_hash(ok_text)
        assert bad_status == "failed" and bad_attempts == 1 and bad_error is not None
    finally:
        _cleanup(factory)
        engine.dispose()


@pytest.mark.db
def test_monotonic_write_preserves_completed_resource() -> None:
    """### A later pending re-persist does not erase extracted attachment content."""
    engine, factory = _factory_or_skip()
    service = ContentProcessingService(factory)
    url = "https://t.test/d.pdf"
    try:
        service.persist(TEST_SOURCE, [_document("https://t.test/d", [_complete(url, "good content")])])
        service.persist(TEST_SOURCE, [_document("https://t.test/d", [_pending(url)])])
        status, _, text, content_hash, _ = _resource_state(factory, _document("https://t.test/d", []))[url]
        assert status == "complete"
        assert text is not None and "good content" in text
        assert content_hash == compute_content_hash(text)
    finally:
        _cleanup(factory)
        engine.dispose()


@pytest.mark.db
def test_failed_resource_retries_until_cap_then_stops() -> None:
    """### A failing attachment retries up to the cap, then stays terminal."""
    engine, factory = _factory_or_skip()
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        raise RuntimeError("still broken")

    service = ContentProcessingService(factory, fetcher=FakeFetcher(fetch), attempt_cap=2)
    document = _document("https://t.test/e", [_pending("https://t.test/e.pdf")])
    try:
        for _ in range(3):
            service.persist(TEST_SOURCE, [document])
        status, attempts, _, _, error = _resource_state(factory, document)["https://t.test/e.pdf"]
        assert attempts == 2
        assert status == "failed"
        assert error is not None
        assert len(calls) == 2  # $ The third pass never attempts past the cap.$
    finally:
        _cleanup(factory)
        engine.dispose()


def _document_row_id(factory, document: CanonicalDocument) -> int:  # type: ignore[no-untyped-def]
    with session_scope(factory) as session:
        source_id = source_id_for(session, TEST_SOURCE)
        assert source_id is not None
        return session.scalars(
            select(Document.id).where(
                Document.source_id == source_id, Document.identity_key == document.identity_key
            )
        ).one()


@pytest.mark.db
def test_claim_is_durable_until_lease_expires() -> None:
    """### A committed claim blocks re-claiming until its lease expires."""
    engine, factory = _factory_or_skip()
    service = ContentProcessingService(factory)  # $ No fetcher: the attachment stays pending.$
    document = _document("https://t.test/claim", [_pending("https://t.test/claim.pdf")])
    try:
        service.persist(TEST_SOURCE, [document])
        document_id = _document_row_id(factory, document)
        now = datetime.now(tz=UTC)
        with session_scope(factory) as session:
            first = claim_due_resources(
                session, attempt_cap=5, document_ids=[document_id], now=now, lease_seconds=300
            )
        assert len(first) == 1
        with session_scope(factory) as session:
            blocked = claim_due_resources(
                session, attempt_cap=5, document_ids=[document_id], now=now, lease_seconds=300
            )
        assert blocked == []
        later = now + timedelta(seconds=301)
        with session_scope(factory) as session:
            reclaimed = claim_due_resources(
                session, attempt_cap=5, document_ids=[document_id], now=later, lease_seconds=300
            )
        assert len(reclaimed) == 1
    finally:
        _cleanup(factory)
        engine.dispose()


@pytest.mark.db
def test_fetcher_receives_the_byte_limit() -> None:
    """### The service hands the byte limit to the fetcher (so it can cap streaming)."""
    engine, factory = _factory_or_skip()
    recording = RecordingFetcher(_make_pdf("ok body"))
    service = ContentProcessingService(factory, fetcher=recording, max_bytes=4096)
    document = _document("https://t.test/limit", [_pending("https://t.test/limit.pdf")])
    try:
        summary = service.persist(TEST_SOURCE, [document])
        assert recording.max_bytes_seen == 4096
        assert summary.extracted == 1
    finally:
        _cleanup(factory)
        engine.dispose()
