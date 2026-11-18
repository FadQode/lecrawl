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

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.crawling.base import redact_message, redact_url
from lexcrawl.db.session import session_scope
from lexcrawl.logging import get_logger
from lexcrawl.models.document import CanonicalDocument, compute_content_hash, ensure_text_marker
from lexcrawl.pipeline.pdf import extract_pdf_text
from lexcrawl.repositories.documents import upsert_documents_batch
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

# $ (text, error) for one payload; the batch extractor never raises, so one bad PDF
# $ cannot fail its siblings.$
ExtractResult = tuple[str | None, str | None]
ResourceExtractor = Callable[[Sequence[bytes]], Sequence[ExtractResult]]


def extract_pdf_text_many(payloads: Sequence[bytes]) -> list[ExtractResult]:
    """### Extract a batch of PDFs in-process, capturing per-item errors."""
    results: list[ExtractResult] = []
    for payload in payloads:
        try:
            results.append((extract_pdf_text(payload), None))
        except Exception as error:
            results.append((None, str(error)))
    return results


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
        extract: ResourceExtractor = extract_pdf_text_many,
    ) -> None:
        """### Store the session factory, fetcher, extraction policy, and batch extractor."""
        self._factory = session_factory
        self._fetcher = fetcher
        self._attempt_cap = max(1, attempt_cap)
        self._max_bytes = max(1, max_bytes)
        self._lease_seconds = max(1, lease_seconds)
        # $ The batch extractor is injectable so the resource queue can run it in a process pool.$
        self._extract_many = extract

    def persist(self, source: str, documents: Sequence[CanonicalDocument]) -> PersistSummary:
        """### Upsert documents, claim due attachments, fetch, then record results."""
        if not documents:
            return PersistSummary()
        now = datetime.now(tz=UTC)
        claims, resources, inserted, updated = self._stage(source, documents, now)
        results = self._extract_batch(claims)
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

    def process_due_resources(self, *, limit: int = 50) -> PersistSummary:
        """### Claim and extract one global batch of due attachments (the resource queue)."""
        if self._fetcher is None:
            return PersistSummary()
        now = datetime.now(tz=UTC)
        with session_scope(self._factory) as session:
            claims = claim_due_resources(
                session,
                attempt_cap=self._attempt_cap,
                now=now,
                lease_seconds=self._lease_seconds,
                limit=limit,
                all_sources=True,
            )
        results = self._extract_batch(claims)
        if results:
            self._apply(results, now)
        extracted = sum(1 for result in results if result.ok)
        return PersistSummary(extracted=extracted, failed=len(results) - extracted)

    def retry_due_resources(self, source: str, *, limit: int = 50) -> PersistSummary:
        """### Claim and extract due attachments for a source, independent of new documents."""
        if self._fetcher is None:
            return PersistSummary()
        now = datetime.now(tz=UTC)
        with session_scope(self._factory) as session:
            source_id = source_id_for(session, source)
            if source_id is None:
                return PersistSummary()
            claims = claim_due_resources(
                session,
                attempt_cap=self._attempt_cap,
                source_id=source_id,
                now=now,
                lease_seconds=self._lease_seconds,
                limit=limit,
            )
        results = self._extract_batch(claims)
        if results:
            self._apply(results, now)
        extracted = sum(1 for result in results if result.ok)
        return PersistSummary(extracted=extracted, failed=len(results) - extracted)

    def _stage(
        self, source: str, documents: Sequence[CanonicalDocument], now: datetime
    ) -> tuple[list[ResourceClaim], int, int, int]:
        """### Short transaction: upsert rows and durably claim due attachments."""
        resources = 0
        with session_scope(self._factory) as session:
            source_id = source_id_for(session, source)
            if source_id is None:
                raise PersistenceError(f"source {source!r} is not registered; run the seed migration")
            counts, ids_by_key = upsert_documents_batch(
                session, source_id=source_id, documents=documents, now=now
            )
            document_ids: list[int] = []
            for document in documents:
                document_id = ids_by_key.get(str(document.identity_key))
                if document_id is None:
                    continue
                document_ids.append(document_id)
                for resource in document.resources:
                    upsert_resource(session, document_id=document_id, resource=resource, now=now)
                    resources += 1
            if self._fetcher is None:
                return [], resources, counts.inserted, counts.updated
            claims = claim_due_resources(
                session,
                attempt_cap=self._attempt_cap,
                document_ids=document_ids,
                now=now,
                lease_seconds=self._lease_seconds,
            )
            return claims, resources, counts.inserted, counts.updated

    def _extract_batch(self, claims: Sequence[ResourceClaim]) -> list[_ExtractionResult]:
        """### Fetch every claim, then extract the whole batch and assemble the results."""
        results: list[_ExtractionResult | None] = [None] * len(claims)
        indexes: list[int] = []
        payloads: list[bytes] = []
        for index, claim in enumerate(claims):
            payload, error = self._fetch_payload(claim)
            if payload is None:
                results[index] = self._failed(claim, error)
            else:
                indexes.append(index)
                payloads.append(payload)
        extracted = list(self._extract_many(payloads)) if payloads else []
        for index, (text, error) in zip(indexes, extracted, strict=True):
            results[index] = self._result(claims[index], text, error)
        return [result for result in results if result is not None]

    def _fetch_payload(self, claim: ResourceClaim) -> tuple[bytes | None, str | None]:
        """### Fetch one attachment's bytes, returning `(payload, error)` without raising."""
        try:
            if self._fetcher is None:
                raise ValueError("no attachment fetcher configured")
            # $ The fetcher enforces max_bytes while streaming, bounding peak memory.$
            data = self._fetcher.fetch(claim.url, max_bytes=self._max_bytes)
            if len(data) > self._max_bytes:
                raise ValueError(f"attachment exceeds the {self._max_bytes}-byte limit")
        except Exception as error:  # $ A fetch failure marks this attachment failed, not the batch.$
            message = redact_message(str(error), limit=MAX_ERROR_LENGTH)
            LOGGER.warning("resource_fetch_failed", extra={"url": redact_url(claim.url), "error": message})
            return None, message
        return data, None

    def _failed(self, claim: ResourceClaim, error: str | None) -> _ExtractionResult:
        """### Build a failed extraction result."""
        return _ExtractionResult(
            claim=claim, ok=False, text=None, content_hash=None, error=error or "unknown error"
        )

    def _result(self, claim: ResourceClaim, text: str | None, error: str | None) -> _ExtractionResult:
        """### Build a result from one batch extraction, validating the text."""
        if text is None or not text.strip():
            message = redact_message(error or "PDF contained no extractable text", limit=MAX_ERROR_LENGTH)
            LOGGER.warning("resource_extraction_failed", extra={"url": redact_url(claim.url), "error": message})
            return self._failed(claim, message)
        marked = ensure_text_marker(text)
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
