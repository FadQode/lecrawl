"""### DB test: `filter_known` runs against real PostgreSQL (regression for the ANY() bind bug)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import delete

from lexcrawl.db.models import Document, Source
from lexcrawl.db.session import session_scope
from lexcrawl.models.document import CanonicalDocument, compute_content_hash
from lexcrawl.repositories.documents import filter_known, upsert_document
from lexcrawl.repositories.sources import ensure_source

TEST_SOURCE = "test_filter_known_source"


def _document(slug: str, *, complete: bool) -> CanonicalDocument:
    body = f"TEXT-BEGIN\n{slug} body"
    return CanonicalDocument(
        source=TEST_SOURCE,
        source_url=f"https://known.test/{slug}",
        title=slug,
        language="en",
        text=body if complete else None,
        content_hash=compute_content_hash(body) if complete else None,
        extraction_status="complete" if complete else "failed",
        extraction_error=None if complete else "boom",
        published_at=date(2026, 1, 1),
        published_precision="day",
    )


def _cleanup(factory) -> None:  # type: ignore[no-untyped-def]
    with session_scope(factory) as session:
        source = session.query(Source).filter(Source.code == TEST_SOURCE).one_or_none()
        if source is not None:
            session.execute(delete(Document).where(Document.source_id == source.id))
            session.execute(delete(Source).where(Source.id == source.id))


@pytest.mark.db
def test_filter_known_returns_only_completed_candidates(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### Only completed documents among the candidates are reported as known."""
    _cleanup(pg_factory)
    now = datetime.now(tz=UTC)
    done, failed = _document("done", complete=True), _document("failed", complete=False)
    try:
        with session_scope(pg_factory) as session:
            source_id = ensure_source(session, TEST_SOURCE, "Filter known source")
            upsert_document(session, source_id=source_id, document=done, now=now)
            upsert_document(session, source_id=source_id, document=failed, now=now)
        candidates = [str(done.identity_key), str(failed.identity_key), "never-seen"]
        # $ Pad past the chunk size so the chunked path runs against real PostgreSQL.$
        candidates += [f"filler-{index}" for index in range(1500)]
        with session_scope(pg_factory) as session:
            known = filter_known(session, source_id=source_id, keys=candidates)
            assert known == frozenset({str(done.identity_key)})
            assert filter_known(session, source_id=source_id, keys=[]) == frozenset()
    finally:
        _cleanup(pg_factory)
