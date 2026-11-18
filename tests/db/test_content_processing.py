"""### PostgreSQL tests for the content processing service."""

from __future__ import annotations

import pymupdf
import pytest
from sqlalchemy import delete, select

from lexcrawl.db.models import Document, Source
from lexcrawl.db.models import DocumentResource as ResourceRow
from lexcrawl.db.session import session_scope
from lexcrawl.models.document import CanonicalDocument, DocumentResource, compute_content_hash
from lexcrawl.repositories.sources import ensure_source, source_id_for
from lexcrawl.services.content_processing import ContentProcessingService

TEST_SOURCE = "test_cp_source"


def _pdf(text: str) -> bytes:
    """### Build a one-page PDF."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


class FakeFetcher:
    """### Returns a valid PDF for the resource fetch."""

    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.closed = False

    def fetch(self, url: str, *, max_bytes: int) -> bytes:
        if not self.ok:
            raise RuntimeError("boom")
        return _pdf("Attachment body")

    def close(self) -> None:
        self.closed = True


def _document(resource: DocumentResource) -> CanonicalDocument:
    """### Build a complete document with one attachment."""
    body = "TEXT-BEGIN\nbody"
    return CanonicalDocument(
        source=TEST_SOURCE, source_url="https://t.test/a", title="T", language="en",
        text=body, content_hash=compute_content_hash(body), extraction_status="complete", resources=(resource,),
    )


def _resources(factory):  # type: ignore[no-untyped-def]
    """### Return the attachments belonging to the test source."""
    with session_scope(factory) as session:
        source_id = source_id_for(session, TEST_SOURCE)
        assert source_id is not None
        return session.scalars(
            select(ResourceRow).where(ResourceRow.document_id.in_(select(Document.id).where(Document.source_id == source_id)))
        ).all()


def _cleanup(factory) -> None:  # type: ignore[no-untyped-def]
    with session_scope(factory) as session:
        source_id = source_id_for(session, TEST_SOURCE)
        if source_id is not None:
            session.execute(delete(Document).where(Document.source_id == source_id))
        session.execute(delete(Source).where(Source.code == TEST_SOURCE))


@pytest.mark.db
def test_persist_extracts_attachment(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### A pending PDF is downloaded, extracted, and marked complete."""
    _cleanup(pg_factory)
    fetcher = FakeFetcher()
    try:
        with session_scope(pg_factory) as session:
            ensure_source(session, TEST_SOURCE, "CP source")
        service = ContentProcessingService(pg_factory, fetcher=fetcher, attempt_cap=2)  # type: ignore[arg-type]
        pending = DocumentResource(title="p", url="https://t.test/a.pdf", kind="pdf", status="pending")
        summary = service.persist(TEST_SOURCE, [_document(pending)])
        assert summary.inserted == 1 and summary.resources == 1 and summary.extracted == 1
        row = _resources(pg_factory)[0]
        assert row.status == "complete" and row.text is not None and row.text.startswith("TEXT-BEGIN")
        assert service.retry_due_resources(TEST_SOURCE).extracted == 0
    finally:
        _cleanup(pg_factory)


@pytest.mark.db
def test_failed_attachment_retries_to_cap(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### A failing attachment retries up to the attempt cap."""
    _cleanup(pg_factory)
    try:
        with session_scope(pg_factory) as session:
            ensure_source(session, TEST_SOURCE, "CP source")
        service = ContentProcessingService(pg_factory, fetcher=FakeFetcher(ok=False), attempt_cap=2)  # type: ignore[arg-type]
        pending = DocumentResource(title="p", url="https://t.test/b.pdf", kind="pdf", status="pending")
        doc = _document(pending)
        for _ in range(3):
            service.persist(TEST_SOURCE, [doc])
        row = _resources(pg_factory)[0]
        assert row.status == "failed" and row.attempts == 2
    finally:
        _cleanup(pg_factory)
