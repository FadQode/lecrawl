"""### Idempotent document reads and writes.

Writes are monotonic: a failed re-fetch never replaces good content, and every
write refreshes the discovery metadata and `last_seen_at`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lexcrawl.db.models import Document
from lexcrawl.models.document import CanonicalDocument

COMPLETE = "complete"
DUE_STATUSES = ("pending", "failed")


@dataclass(frozen=True, slots=True)
class UpsertCounts:
    """### How many documents were inserted versus refreshed."""

    inserted: int = 0
    updated: int = 0


def _published_precision(document: CanonicalDocument) -> str:
    """### Resolve the stored precision, treating an exact date as day precision."""
    if document.published_precision is not None:
        return document.published_precision
    if document.published_at is not None:
        return "day"
    if document.published_month is not None:
        return "month"
    if document.published_year is not None:
        return "year"
    return "unknown"


def _values(document: CanonicalDocument, source_id: int) -> dict[str, Any]:
    """### Map a canonical document to document column values."""
    return {
        "source_id": source_id,
        "identity_key": document.identity_key,
        "native_id": document.native_id,
        "source_url": document.source_url,
        "title": document.title,
        "language": document.language,
        "document_type": document.type_of_law,
        "summary": document.summary,
        "image_url": document.image_url,
        "published_at": document.published_at,
        "published_month": document.published_month,
        "published_year": document.published_year,
        "published_date_raw": document.published_date_raw,
        "published_precision": _published_precision(document),
        "topics": list(document.topics),
        "extraction_status": document.extraction_status,
        "extraction_error": document.extraction_error,
        "text": document.text,
        "content_hash": document.content_hash,
    }


def _is_successful(document: CanonicalDocument) -> bool:
    """### Report whether a document carries a completed extraction."""
    return document.extraction_status == COMPLETE and document.text is not None


def upsert_document_with_status(
    session: Session, *, source_id: int, document: CanonicalDocument, now: datetime
) -> tuple[int, bool]:
    """### Insert or refresh one document; return (id, was_inserted).

    The write is guarded by the `(source_id, identity_key)` unique constraint and applies
    the monotonic content rule (a failed re-fetch never replaces good content). The
    documented worker model is one writer per database; batched `ON CONFLICT` upserts are
    deferred until measured volume justifies them.
    """
    values = _values(document, source_id)
    existing = session.scalar(
        select(Document).where(
            Document.source_id == source_id, Document.identity_key == str(values["identity_key"])
        )
    )
    if existing is None:
        row = Document(
            **values,
            extraction_attempts=0 if _is_successful(document) else 1,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return row.id, True

    if _is_successful(document) or existing.extraction_status != COMPLETE:
        existing.text = values["text"]
        existing.content_hash = values["content_hash"]
        existing.extraction_status = values["extraction_status"]
        existing.extraction_error = values["extraction_error"]
    if not _is_successful(document):
        existing.extraction_attempts = min(existing.extraction_attempts + 1, 32767)

    existing.native_id = values["native_id"]
    existing.source_url = str(values["source_url"])
    existing.title = str(values["title"])
    existing.language = str(values["language"])
    existing.document_type = values["document_type"]
    existing.summary = values["summary"] or existing.summary
    existing.image_url = values["image_url"] or existing.image_url
    existing.published_at = values["published_at"] or existing.published_at
    existing.published_month = values["published_month"] or existing.published_month
    existing.published_year = values["published_year"] or existing.published_year
    existing.published_date_raw = values["published_date_raw"] or existing.published_date_raw
    if values["published_precision"] != "unknown":
        existing.published_precision = str(values["published_precision"])
    existing.topics = list(values["topics"]) or list(existing.topics)
    existing.last_seen_at = now
    existing.updated_at = now
    session.flush()
    return existing.id, False


def upsert_document(session: Session, *, source_id: int, document: CanonicalDocument, now: datetime) -> int:
    """### Insert or refresh one document and return its id."""
    return upsert_document_with_status(session, source_id=source_id, document=document, now=now)[0]


def upsert_documents(
    session: Session, *, source_id: int, documents: Sequence[CanonicalDocument], now: datetime
) -> UpsertCounts:
    """### Upsert every document, returning inserted and updated counts."""
    inserted = 0
    for document in documents:
        _, was_inserted = upsert_document_with_status(
            session, source_id=source_id, document=document, now=now
        )
        if was_inserted:
            inserted += 1
    return UpsertCounts(inserted=inserted, updated=len(documents) - inserted)


def known_identities(session: Session, *, source_id: int) -> frozenset[str]:
    """### Return the identities whose extraction already completed."""
    rows = session.scalars(
        select(Document.identity_key).where(
            Document.source_id == source_id, Document.extraction_status == COMPLETE
        )
    )
    return frozenset(rows)


def count_due_documents(session: Session, *, source_id: int, attempt_cap: int) -> int:
    """### Count documents still due for retry within the attempt cap."""
    total = session.scalar(
        select(func.count())
        .select_from(Document)
        .where(
            Document.source_id == source_id,
            Document.extraction_status.in_(DUE_STATUSES),
            Document.extraction_attempts < attempt_cap,
        )
    )
    return int(total or 0)
