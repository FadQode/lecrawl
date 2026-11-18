"""### PostgreSQL test for the DB-backed document memory."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from lexcrawl.db.models import Document, Source
from lexcrawl.db.session import session_scope
from lexcrawl.models.document import CanonicalDocument, RawArticle
from lexcrawl.repositories.memory import DbDocumentMemory
from lexcrawl.repositories.sources import ensure_source, source_id_for

TEST_SOURCE = "test_memory_source"


@pytest.mark.db
def test_document_memory_roundtrip(pg_engine, pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### remember persists documents, known reflects them, and sweeps are no-ops without a fetcher."""
    with session_scope(pg_factory) as session:
        session.execute(delete(Source).where(Source.code == TEST_SOURCE))
    with session_scope(pg_factory) as session:
        ensure_source(session, TEST_SOURCE, "Memory source")
    memory = DbDocumentMemory(pg_engine, attempt_cap=3)
    try:
        assert memory.known(TEST_SOURCE) == frozenset()
        document = CanonicalDocument.from_raw(
            RawArticle(source=TEST_SOURCE, url="https://t.test/a", title="T", language="en", paragraphs=["b"])
        )
        assert memory.remember(TEST_SOURCE, [document]).inserted == 1
        assert document.identity_key in memory.known(TEST_SOURCE)
        assert memory.due_count(TEST_SOURCE) == 0
        assert memory.retry_due(TEST_SOURCE) == 0
    finally:
        with session_scope(pg_factory) as session:
            source_id = source_id_for(session, TEST_SOURCE)
            if source_id is not None:
                session.execute(delete(Document).where(Document.source_id == source_id))
            session.execute(delete(Source).where(Source.code == TEST_SOURCE))
        memory.close()
