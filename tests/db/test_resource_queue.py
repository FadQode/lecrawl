"""### DB test for the attachment extraction path used by the resource queue."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import delete, select

from lexcrawl.db.models import Document, DocumentResource, Source
from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.models.document import CanonicalDocument, compute_content_hash
from lexcrawl.models.document import DocumentResource as CanonicalResource
from lexcrawl.repositories.sources import ensure_source, source_id_for
from lexcrawl.services.content_processing import ContentProcessingService

TEST_SOURCE = "test_resource_queue"


class _FakeFetcher:
    """### Return a fixed PDF payload without touching the network."""

    def __init__(self, payload: bytes) -> None:
        """### Store the payload to return."""
        self._payload = payload

    def fetch(self, url: str, *, max_bytes: int) -> bytes:
        """### Return the stored payload."""
        return self._payload

    def close(self) -> None:
        """### No-op close."""
        return None


def _document(title: str, resource: CanonicalResource) -> CanonicalDocument:
    """### Build a complete document carrying one PDF attachment."""
    body = f"TEXT-BEGIN\n{title} body"
    return CanonicalDocument(
        source=TEST_SOURCE,
        source_url=f"https://r.test/{title.lower()}",
        title=title,
        language="en",
        text=body,
        content_hash=compute_content_hash(body),
        extraction_status="complete",
        published_at=date(2026, 1, 1),
        published_precision="day",
        resources=(resource,),
    )


def _cleanup(factory) -> None:  # type: ignore[no-untyped-def]
    """### Remove this test's source, documents, and resources."""
    with session_scope(factory) as session:
        source_id = source_id_for(session, TEST_SOURCE)
        if source_id is not None:
            session.execute(delete(Document).where(Document.source_id == source_id))
        session.execute(delete(Source).where(Source.code == TEST_SOURCE))


@pytest.mark.db
def test_resource_extraction_marks_complete(pg_engine, pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### A due PDF is claimed, extracted through the injectable extractor, and completed."""
    _cleanup(pg_factory)
    try:
        attachment = CanonicalResource(title="Report", url="https://r.test/a.pdf", kind="pdf")
        with session_scope(pg_factory) as session:
            ensure_source(session, TEST_SOURCE, "Resource queue")
        # $ Stage the document and its pending attachment (no fetcher yet).$
        stager = ContentProcessingService(create_session_factory(pg_engine))
        stager.persist(TEST_SOURCE, [_document("Report", attachment)])

        service = ContentProcessingService(
            create_session_factory(pg_engine),
            fetcher=_FakeFetcher(b"%PDF-1.7 fake"),
            extract=lambda payloads: [("TEXT-BEGIN\nextracted", None) for _ in payloads],
        )
        summary = service.retry_due_resources(TEST_SOURCE)
        assert summary.extracted == 1 and summary.failed == 0
        with session_scope(pg_factory) as session:
            row = session.scalar(
                select(DocumentResource).where(DocumentResource.url == "https://r.test/a.pdf")
            )
            assert row is not None and row.status == "complete"
            assert (row.text or "").startswith("TEXT-BEGIN")
    finally:
        _cleanup(pg_factory)
