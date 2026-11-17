"""### Database-backed identity memory: known identities derive from stored documents."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Engine

from lexcrawl.checks.identities import RememberResult
from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.models.document import CanonicalDocument
from lexcrawl.repositories.documents import count_due_documents, known_identities
from lexcrawl.repositories.errors import PersistenceError  # noqa: F401  # $ Re-exported for callers.$
from lexcrawl.repositories.sources import source_id_for
from lexcrawl.services.content_processing import ContentProcessingService
from lexcrawl.services.fetchers import ResourceFetcher


class DbDocumentMemory:
    """### Serves the DocumentMemory interface from the PostgreSQL documents table."""

    def __init__(
        self,
        engine: Engine,
        *,
        fetcher: ResourceFetcher | None = None,
        attempt_cap: int = 3,
        max_bytes: int = 20_000_000,
        lease_seconds: int = 300,
        document_attempt_cap: int = 3,
        owns_engine: bool = True,
    ) -> None:
        """### Bind to an engine and build the processing service."""
        self._engine = engine
        self._owns_engine = owns_engine
        self._document_attempt_cap = max(1, document_attempt_cap)
        factory = create_session_factory(engine)
        self._factory = factory
        self._service = ContentProcessingService(
            factory,
            fetcher=fetcher,
            attempt_cap=attempt_cap,
            max_bytes=max_bytes,
            lease_seconds=lease_seconds,
        )

    def known(self, source: str) -> frozenset[str]:
        """### Return identities whose extraction already completed for a source."""
        with session_scope(self._factory) as session:
            source_id = source_id_for(session, source)
            if source_id is None:
                return frozenset()
            return known_identities(session, source_id=source_id)

    def remember(self, source: str, documents: Sequence[CanonicalDocument]) -> RememberResult:
        """### Upsert the processed documents (and their resources) durably."""
        if not documents:
            return RememberResult()
        summary = self._service.persist(source, documents)
        return RememberResult(
            documents=summary.documents,
            inserted=summary.inserted,
            updated=summary.updated,
            resources=summary.resources,
        )

    def due_count(self, source: str) -> int:
        """### Return how many documents are still due for retry."""
        with session_scope(self._factory) as session:
            source_id = source_id_for(session, source)
            if source_id is None:
                return 0
            return count_due_documents(session, source_id=source_id, attempt_cap=self._document_attempt_cap)

    def close(self) -> None:
        """### Dispose the engine's pool only when this component owns it."""
        if self._owns_engine:
            self._engine.dispose()
