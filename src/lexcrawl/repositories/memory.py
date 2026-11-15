"""### Database-backed identity memory: known identities derive from stored documents."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Engine

from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.models.document import CanonicalDocument
from lexcrawl.repositories.documents import known_identities, upsert_documents
from lexcrawl.repositories.sources import source_id_for


class PersistenceError(RuntimeError):
    """### Raised when the persistence layer cannot complete a write."""


class DbDocumentMemory:
    """### Serves the DocumentMemory interface from the PostgreSQL documents table."""

    def __init__(self, engine: Engine) -> None:
        """### Bind to an engine and build a session factory."""
        self._engine = engine
        self._factory = create_session_factory(engine)

    def known(self, source: str) -> frozenset[str]:
        """### Return identities whose extraction already completed for a source."""
        with session_scope(self._factory) as session:
            source_id = source_id_for(session, source)
            if source_id is None:
                return frozenset()
            return known_identities(session, source_id=source_id)

    def remember(self, source: str, documents: Sequence[CanonicalDocument]) -> int:
        """### Upsert the processed documents, committing one transaction."""
        if not documents:
            return 0
        now = datetime.now(tz=UTC)
        with session_scope(self._factory) as session:
            source_id = source_id_for(session, source)
            if source_id is None:
                raise PersistenceError(f"source {source!r} is not registered; run the seed migration")
            return upsert_documents(session, source_id=source_id, documents=documents, now=now)

    def close(self) -> None:
        """### Dispose the engine's connection pool."""
        self._engine.dispose()
