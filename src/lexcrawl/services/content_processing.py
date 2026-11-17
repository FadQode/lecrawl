"""### Content processing service: the extraction-to-persistence boundary.

Normalization, validation, and hashing already live in the canonical models; this
service orchestrates persistence and drives attachment extraction. Network fetches
happen **outside** any database transaction: a short transaction stages documents and
durably claims due attachments (incrementing `attempts` and setting a `claimed_at`
lease), then the bytes are fetched with no transaction held, and a final short
transaction records each result. The claim lease lets other workers skip a row being
fetched and lets a crashed worker's claim expire.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.crawling.base import redact_message, redact_url
from lexcrawl.db.session import session_scope
from lexcrawl.logging import get_logger
from lexcrawl.models.document import CanonicalDocument, compute_content_hash, ensure_text_marker
from lexcrawl.pipeline.pdf import extract_pdf_text
from lexcrawl.repositories.documents import upsert_document_with_status
from lexcrawl.repositories.errors import PersistenceError
from lexcrawl.repositories.resources import (
    ResourceClaim,
    claim_due_resources,
    mark_resource_extracted,
    mark_resource_failed,
    upsert_resource,
)
from lexcrawl.repositories.sources import source_id_for
from lexcrawl.services.fetchers import ResourceFetcher

LOGGER = get_logger(__name__)

MAX_ERROR_LENGTH = 2000
DEFAULT_MAX_BYTES = 20_000_000
DEFAULT_LEASE_SECONDS = 300


@dataclass(frozen=True, slots=True)
class PersistSummary:
    """### Counts describing one persistence pass."""

    documents: int = 0
    resources: int = 0
    extracted: int = 0
    failed: int = 0
    inserted: int = 0
    updated: int = 0


@dataclass(frozen=True, slots=True)
class _ExtractionResult:
    """### Outcome of extracting one claimed attachment."""

    claim: ResourceClaim
    ok: bool
    text: str | None
    content_hash: str | None
    error: str | None


class ContentProcessingService:
    """### Persist canonical documents and process their attachments."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        fetcher: ResourceFetcher | None = None,
        attempt_cap: int = 3,
        max_bytes: int = DEFAULT_MAX_BYTES,
        lease_seconds: int = DEFAULT_LEASE_SECONDS,
    ) -> None:
        """### Store the session factory, fetcher, and extraction policy."""
        self._factory = session_factory
        self._fetcher = fetcher
        self._attempt_cap = max(1, attempt_cap)
        self._max_bytes = max(1, max_bytes)
        self._lease_seconds = max(1, lease_seconds)

    def persist(self, source: str, documents: Sequence[CanonicalDocument]) -> PersistSummary:
        """### Upsert documents, claim due attachments, fetch, then record results."""
        if not documents:
            return PersistSummary()
        now = datetime.now(tz=UTC)
        claims, resources, inserted, updated = self._stage(source, documents, now)
        results = [self._extract(claim) for claim in claims]
        if results:
            self._apply(results, now)
        extracted = sum(1 for result in results if result.ok)
        return PersistSummary(
            documents=len(documents),
            resources=resources,
            extracted=extracted,
            failed=len(results) - extracted,
            inserted=inserted,
            updated=updated,
        )

    def _stage(
        self, source: str, documents: Sequence[CanonicalDocument], now: datetime
    ) -> tuple[list[ResourceClaim], int, int, int]:
        """### Short transaction: upsert rows and durably claim due attachments."""
        resources = 0
        inserted = 0
        updated = 0
        with session_scope(self._factory) as session:
            source_id = source_id_for(session, source)
            if source_id is None:
                raise PersistenceError(f"source {source!r} is not registered; run the seed migration")
            document_ids: list[int] = []
            for document in documents:
                document_id, was_inserted = upsert_document_with_status(
                    session, source_id=source_id, document=document, now=now
                )
                document_ids.append(document_id)
                if was_inserted:
                    inserted += 1
                else:
                    updated += 1
                for resource in document.resources:
                    upsert_resource(session, document_id=document_id, resource=resource, now=now)
                    resources += 1
            if self._fetcher is None:
                return [], resources, inserted, updated
            claims = claim_due_resources(
                session,
                attempt_cap=self._attempt_cap,
                document_ids=document_ids,
                now=now,
                lease_seconds=self._lease_seconds,
            )
            return claims, resources, inserted, updated

    def _extract(self, claim: ResourceClaim) -> _ExtractionResult:
        """### Fetch and extract one attachment with no transaction held."""
        try:
            if self._fetcher is None:
                raise ValueError("no attachment fetcher configured")
            # $ The fetcher enforces max_bytes while streaming, bounding peak memory.$
            data = self._fetcher.fetch(claim.url, max_bytes=self._max_bytes)
            if len(data) > self._max_bytes:
                raise ValueError(f"attachment exceeds the {self._max_bytes}-byte limit")
            body = extract_pdf_text(data)
            if not body.strip():
                raise ValueError("PDF contained no extractable text")
        except Exception as error:  # $ Any fetch/extract failure marks this attachment failed.$
            message = redact_message(str(error), limit=MAX_ERROR_LENGTH)
            LOGGER.warning("resource_extraction_failed", extra={"url": redact_url(claim.url), "error": message})
            return _ExtractionResult(claim=claim, ok=False, text=None, content_hash=None, error=message)
        marked = ensure_text_marker(body)
        return _ExtractionResult(
            claim=claim, ok=True, text=marked, content_hash=compute_content_hash(marked), error=None
        )

    def _apply(self, results: Sequence[_ExtractionResult], now: datetime) -> None:
        """### Short transaction: record each extraction result and clear the claim."""
        with session_scope(self._factory) as session:
            for result in results:
                if result.ok and result.text is not None and result.content_hash is not None:
                    mark_resource_extracted(
                        session,
                        resource_id=result.claim.id,
                        text=result.text,
                        content_hash=result.content_hash,
                        now=now,
                    )
                else:
                    mark_resource_failed(
                        session, resource_id=result.claim.id, error=result.error or "unknown error", now=now
                    )
