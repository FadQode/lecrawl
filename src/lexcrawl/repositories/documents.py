"""### Document repository: canonical documents to `documents` rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lexcrawl.db.models import Document, DocumentLocalization, Source
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
            # $ A completed extraction resets the counter; an incomplete one advances it.$
            "extraction_attempts": case(
                (insert_statement.excluded.extraction_status == "complete", 0),
                else_=Document.extraction_attempts + 1,
            ),
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


def known_identity_keys(session: Session, *, source_code: str) -> set[str]:
    """### Return every persisted identity key for one source."""
    statement = (
        select(Document.identity_key).join(Source, Document.source_id == Source.id).where(Source.code == source_code)
    )
    return set(session.execute(statement).scalars())


@dataclass(frozen=True, slots=True)
class IncompleteReport:
    """### One persisted report whose detail extraction is not complete yet."""

    identity: str
    url: str
    title: str
    published_date_raw: str | None
    published_month: int | None
    published_year: int | None
    attempts: int


def incomplete_reports(
    session: Session,
    *,
    source_code: str,
    max_attempts: int,
    limit: int | None = None,
) -> list[IncompleteReport]:
    """### Return reports whose bounded retry budget is not yet spent."""
    primary_title = (
        select(DocumentLocalization.title)
        .where(DocumentLocalization.document_id == Document.id)
        .order_by(DocumentLocalization.id)
        .limit(1)
        .scalar_subquery()
    )
    statement = (
        select(
            Document.identity_key,
            Document.source_url,
            primary_title,
            Document.published_date_raw,
            Document.published_month,
            Document.published_year,
            Document.extraction_attempts,
        )
        .join(Source, Document.source_id == Source.id)
        .where(
            Source.code == source_code,
            # $ NULL status is treated as incomplete so legacy rows are not skipped.$
            (Document.extraction_status.is_(None)) | (Document.extraction_status != "complete"),
            Document.extraction_attempts < max_attempts,
        )
        .order_by(Document.updated_at)
    )
    if limit is not None:
        statement = statement.limit(limit)
    reports: list[IncompleteReport] = []
    for identity, url, title, date_raw, month, year, attempts in session.execute(statement).all():
        reports.append(
            IncompleteReport(
                identity=str(identity),
                url=str(url),
                title=str(title).strip() if title else str(url),
                published_date_raw=str(date_raw) if date_raw else None,
                published_month=int(month) if month is not None else None,
                published_year=int(year) if year is not None else None,
                attempts=int(attempts),
            )
        )
    return reports
