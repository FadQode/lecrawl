"""### PostgreSQL integration tests for the documents repository.

These require a running database with the migration applied. They skip cleanly when
no database is reachable, so the unit suite still runs without PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from lexcrawl.config import get_settings
from lexcrawl.db.engine import create_db_engine
from lexcrawl.db.models import Document, Source
from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.models.document import CanonicalDocument, compute_content_hash
from lexcrawl.repositories.documents import known_identities, upsert_document, upsert_documents
from lexcrawl.repositories.sources import ensure_source, source_id_for

if TYPE_CHECKING:
    from sqlalchemy import Engine
    from sqlalchemy.orm import Session, sessionmaker

TEST_SOURCE = "test_repository_source"
MARKER = "TEXT-BEGIN"


def _factory_or_skip() -> tuple[Engine, sessionmaker[Session]]:
    """### Return an engine and session factory, skipping when PostgreSQL is unreachable."""
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
    return engine, create_session_factory(engine)


def _document(*, url: str, text: str | None, status: str) -> CanonicalDocument:
    """### Build a canonical document for persistence tests."""
    body = f"{MARKER}\n{text}" if text else None
    return CanonicalDocument(
        source=TEST_SOURCE,
        source_url=url,
        title="Test document",
        language="en",
        text=body,
        content_hash=compute_content_hash(body) if body else None,
        extraction_status=status,  # type: ignore[arg-type]
    )


def _cleanup(factory: sessionmaker[Session]) -> None:
    """### Remove the test source and its documents."""
    with session_scope(factory) as session:
        source_id = source_id_for(session, TEST_SOURCE)
        if source_id is not None:
            session.execute(delete(Document).where(Document.source_id == source_id))
            session.execute(delete(Source).where(Source.code == TEST_SOURCE))


@pytest.mark.db
def test_upsert_is_idempotent_and_known_reflects_complete() -> None:
    """### Upserting twice keeps one row; only complete documents become known."""
    engine, factory = _factory_or_skip()
    now = datetime.now(tz=UTC)
    try:
        with session_scope(factory) as session:
            source_id = ensure_source(session, TEST_SOURCE, "Test source")
            first = upsert_document(
                session,
                source_id=source_id,
                document=_document(url="https://t.test/a", text="body", status="complete"),
                now=now,
            )
            second = upsert_document(
                session,
                source_id=source_id,
                document=_document(url="https://t.test/a", text="body", status="complete"),
                now=now,
            )
            assert first == second

        with session_scope(factory) as session:
            source_id = source_id_for(session, TEST_SOURCE)
            assert source_id is not None
            rows = session.scalars(select(Document).where(Document.source_id == source_id)).all()
            assert len(rows) == 1
            assert rows[0].extraction_status == "complete"
            assert rows[0].identity_key in known_identities(session, source_id=source_id)
    finally:
        _cleanup(factory)
        engine.dispose()


@pytest.mark.db
def test_monotonic_write_preserves_good_content() -> None:
    """### A failed re-fetch does not overwrite already-complete content."""
    engine, factory = _factory_or_skip()
    now = datetime.now(tz=UTC)
    try:
        with session_scope(factory) as session:
            source_id = ensure_source(session, TEST_SOURCE, "Test source")
            upsert_document(
                session,
                source_id=source_id,
                document=_document(url="https://t.test/b", text="body", status="complete"),
                now=now,
            )
            upsert_document(
                session,
                source_id=source_id,
                document=_document(url="https://t.test/b", text=None, status="failed"),
                now=now,
            )
        with session_scope(factory) as session:
            source_id = source_id_for(session, TEST_SOURCE)
            assert source_id is not None
            row = session.scalars(select(Document).where(Document.source_id == source_id)).one()
            assert row.extraction_status == "complete"
            assert row.text is not None and row.text.startswith(MARKER)
            assert row.extraction_attempts == 1
    finally:
        _cleanup(factory)
        engine.dispose()


@pytest.mark.db
def test_upsert_documents_counts() -> None:
    """### Batch upsert writes every document."""
    engine, factory = _factory_or_skip()
    now = datetime.now(tz=UTC)
    try:
        with session_scope(factory) as session:
            source_id = ensure_source(session, TEST_SOURCE, "Test source")
            written = upsert_documents(
                session,
                source_id=source_id,
                documents=[
                    _document(url="https://t.test/c", text="c", status="complete"),
                    _document(url="https://t.test/d", text=None, status="failed"),
                ],
                now=now,
            )
            assert written == 2
    finally:
        _cleanup(factory)
        engine.dispose()
