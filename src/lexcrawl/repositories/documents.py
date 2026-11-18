"""### Idempotent document reads and writes.

Writes are monotonic: a failed re-fetch never replaces good content, and every
write refreshes the discovery metadata and `last_seen_at`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Text, any_, bindparam, func, literal_column, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from lexcrawl.db.models import Document
from lexcrawl.models.document import CanonicalDocument

COMPLETE = "complete"
DUE_STATUSES = ("pending", "failed")

# $ The monotonic content rule, expressed for ON CONFLICT: a failed re-fetch never
# $ replaces good content, and only an unsuccessful pass increments attempts.$
_INCOMING_SUCCESS = "(excluded.extraction_status = 'complete' AND excluded.text IS NOT NULL)"
_TAKE_CONTENT = f"({_INCOMING_SUCCESS} OR documents.extraction_status IS DISTINCT FROM 'complete')"
_MONOTONIC_SET: dict[Any, Any] = {
    "text": text(f"CASE WHEN {_TAKE_CONTENT} THEN excluded.text ELSE documents.text END"),
    "content_hash": text(f"CASE WHEN {_TAKE_CONTENT} THEN excluded.content_hash ELSE documents.content_hash END"),
    "extraction_status": text(
        f"CASE WHEN {_TAKE_CONTENT} THEN excluded.extraction_status ELSE documents.extraction_status END"
    ),
    "extraction_error": text(
        f"CASE WHEN {_TAKE_CONTENT} THEN excluded.extraction_error ELSE documents.extraction_error END"
    ),
    "extraction_attempts": text(
        f"CASE WHEN NOT {_INCOMING_SUCCESS} "
        "THEN LEAST(documents.extraction_attempts + 1, 32767) "
        "ELSE documents.extraction_attempts END"
    ),
    "native_id": text("excluded.native_id"),
    "source_url": text("excluded.source_url"),
    "title": text("excluded.title"),
    "language": text("excluded.language"),
    "document_type": text("excluded.document_type"),
    "summary": text("COALESCE(excluded.summary, documents.summary)"),
    "image_url": text("COALESCE(excluded.image_url, documents.image_url)"),
    "published_at": text("COALESCE(excluded.published_at, documents.published_at)"),
    "published_month": text("COALESCE(excluded.published_month, documents.published_month)"),
    "published_year": text("COALESCE(excluded.published_year, documents.published_year)"),
    "published_date_raw": text("COALESCE(excluded.published_date_raw, documents.published_date_raw)"),
    "published_precision": text(
        "CASE WHEN excluded.published_precision <> 'unknown' "
        "THEN excluded.published_precision ELSE documents.published_precision END"
    ),
    "topics": text(
        "CASE WHEN cardinality(excluded.topics) > 0 THEN excluded.topics ELSE documents.topics END"
    ),
    "last_seen_at": text("excluded.last_seen_at"),
    "updated_at": text("excluded.updated_at"),
}


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
    """### Upsert every document in one statement, returning inserted and updated counts."""
    counts, _ = upsert_documents_batch(session, source_id=source_id, documents=documents, now=now)
    return counts


def _upsert_row(document: CanonicalDocument, source_id: int, now: datetime) -> dict[str, Any]:
    """### Build one INSERT row, including the insert-only bookkeeping columns."""
    values = _values(document, source_id)
    values.update(
        {
            "extraction_attempts": 0 if _is_successful(document) else 1,
            "first_seen_at": now,
            "last_seen_at": now,
            "created_at": now,
            "updated_at": now,
        }
    )
    return values


def upsert_documents_batch(
    session: Session, *, source_id: int, documents: Sequence[CanonicalDocument], now: datetime
) -> tuple[UpsertCounts, dict[str, int]]:
    """### Upsert a page of documents in one `INSERT ... ON CONFLICT`, returning their ids.

    The monotonic rule is applied in the `DO UPDATE` clause so the write stays a single
    round trip; `xmax = 0` distinguishes a fresh insert from a refresh.
    """
    rows: dict[str, dict[str, Any]] = {}
    for document in documents:
        rows[str(document.identity_key)] = _upsert_row(document, source_id, now)
    if not rows:
        return UpsertCounts(), {}
    statement: Any = (
        pg_insert(Document)
        .values(list(rows.values()))
        .on_conflict_do_update(index_elements=["source_id", "identity_key"], set_=_MONOTONIC_SET)
        .returning(Document.id, Document.identity_key, literal_column("(xmax = 0)").label("inserted"))
    )
    ids: dict[str, int] = {}
    inserted = 0
    for row in session.execute(statement).all():
        ids[str(row.identity_key)] = int(row.id)
        if row.inserted:
            inserted += 1
    return UpsertCounts(inserted=inserted, updated=len(rows) - inserted), ids


def filter_known(session: Session, *, source_id: int, keys: Sequence[str]) -> frozenset[str]:
    """### Return which of the candidate identities already have a completed extraction.

    Uses `identity_key = ANY(:keys)` so the lookup scans only the candidates (plus the
    `(source_id, identity_key)` index) instead of every stored document for the source.
    """
    candidates = list(dict.fromkeys(str(key) for key in keys))
    if not candidates:
        return frozenset()
    rows = session.scalars(
        select(Document.identity_key).where(
            Document.source_id == source_id,
            Document.identity_key == any_(bindparam("keys", type_=Text())),
            Document.extraction_status == COMPLETE,
        ),
        {"keys": candidates},
    )
    return frozenset(rows)


def has_baseline(session: Session, *, source_id: int) -> bool:
    """### Report whether the source has at least one completed document."""
    return bool(
        session.scalar(
            select(Document.id)
            .where(Document.source_id == source_id, Document.extraction_status == COMPLETE)
            .limit(1)
        )
    )


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
