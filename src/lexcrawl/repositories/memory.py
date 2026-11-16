"""### Database-backed identity memory: known identities derive from stored documents."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Engine

from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.models.document import CanonicalDocument
from lexcrawl.repositories.documents import known_identities
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
    ) -> None:
        """### Bind to an engine and build the processing service."""
        self._engine = engine
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

    def remember(self, source: str, documents: Sequence[CanonicalDocument]) -> int:
        """### Upsert the processed documents (and their resources) durably."""
        if not documents:
            return 0
        return self._service.persist(source, documents).documents

    def close(self) -> None:
        """### Dispose the engine's connection pool."""
        self._engine.dispose()
