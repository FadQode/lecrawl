"""### Document repository: canonical documents to `documents` rows."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lexcrawl.db.models import Document
from lexcrawl.models.document import CanonicalDocument
from lexcrawl.repositories.mapping import document_values


def upsert_document(
    session: Session,
    *,
    source_id: int,
    document: CanonicalDocument,
    now: datetime,
) -> int:
    """### Insert or update one logical document and return its identifier."""
    values = document_values(document, now=now) | {"source_id": source_id}
    insert_statement = insert(Document).values(**values)
    statement = insert_statement.on_conflict_do_update(
        index_elements=[Document.source_id, Document.identity_key],
        set_={
            # $ Nullable metadata is preserved when a later observation lacks it.$
            "native_id": func.coalesce(insert_statement.excluded.native_id, Document.native_id),
            "source_url": insert_statement.excluded.source_url,
            "listing_url": func.coalesce(insert_statement.excluded.listing_url, Document.listing_url),
            "document_type": insert_statement.excluded.document_type,
            "published_at": func.coalesce(insert_statement.excluded.published_at, Document.published_at),
            "published_year": func.coalesce(insert_statement.excluded.published_year, Document.published_year),
            "published_month": func.coalesce(insert_statement.excluded.published_month, Document.published_month),
            "published_precision": insert_statement.excluded.published_precision,
            "published_date_raw": func.coalesce(
                insert_statement.excluded.published_date_raw, Document.published_date_raw
            ),
            "topics": insert_statement.excluded.topics,
            "image_url": func.coalesce(insert_statement.excluded.image_url, Document.image_url),
            "source_created_at": func.coalesce(insert_statement.excluded.source_created_at, Document.source_created_at),
            "provenance": insert_statement.excluded.provenance,
            "archive_url": func.coalesce(insert_statement.excluded.archive_url, Document.archive_url),
            "archived_at": func.coalesce(insert_statement.excluded.archived_at, Document.archived_at),
            "archive_listing_url": func.coalesce(
                insert_statement.excluded.archive_listing_url, Document.archive_listing_url
            ),
            "extraction_status": insert_statement.excluded.extraction_status,
            "extraction_error": insert_statement.excluded.extraction_error,
            "last_seen_at": now,
            "updated_at": now,
        },
    ).returning(Document.id)
    return int(session.execute(statement).scalar_one())


def find_document(session: Session, *, source_id: int, identity_key: str) -> Document | None:
    """### Return one document by its source-scoped identity."""
    return session.execute(
        select(Document).where(Document.source_id == source_id, Document.identity_key == identity_key)
    ).scalar_one_or_none()
