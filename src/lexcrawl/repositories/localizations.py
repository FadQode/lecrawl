"""### Localization repository: per-language content and attempt state."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lexcrawl.db.models import DocumentLocalization
from lexcrawl.repositories.mapping import LocalizationPayload


def upsert_localization(
    session: Session,
    *,
    document_id: int,
    payload: LocalizationPayload,
    now: datetime,
) -> int:
    """### Insert or update one localization, preserving the last successful content."""
    values = {
        "document_id": document_id,
        "language_code": payload.language_code,
        "title": payload.title,
        "summary": payload.summary,
        "text_content": payload.text_content,
        "content_hash": payload.content_hash,
        "status": payload.status,
        "source_url": payload.source_url,
        "listing_url": payload.listing_url,
        "published_at": payload.published_at,
        "image_url": payload.image_url,
        "error": payload.error,
        "fetched_at": now if payload.status == "complete" else None,
        "last_attempt_at": now,
        "created_at": now,
        "updated_at": now,
    }
    insert_statement = insert(DocumentLocalization).values(**values)
    statement = insert_statement.on_conflict_do_update(
        index_elements=[DocumentLocalization.document_id, DocumentLocalization.language_code],
        set_={
            # $ Content is only ever replaced by newer content, never by an empty attempt.$
            "title": func.coalesce(insert_statement.excluded.title, DocumentLocalization.title),
            "summary": func.coalesce(insert_statement.excluded.summary, DocumentLocalization.summary),
            "text_content": func.coalesce(insert_statement.excluded.text_content, DocumentLocalization.text_content),
            "content_hash": func.coalesce(insert_statement.excluded.content_hash, DocumentLocalization.content_hash),
            "status": insert_statement.excluded.status,
            "source_url": func.coalesce(insert_statement.excluded.source_url, DocumentLocalization.source_url),
            "listing_url": func.coalesce(insert_statement.excluded.listing_url, DocumentLocalization.listing_url),
            "published_at": func.coalesce(insert_statement.excluded.published_at, DocumentLocalization.published_at),
            "image_url": func.coalesce(insert_statement.excluded.image_url, DocumentLocalization.image_url),
            "error": insert_statement.excluded.error,
            "fetched_at": case(
                (insert_statement.excluded.status == "complete", insert_statement.excluded.last_attempt_at),
                else_=DocumentLocalization.fetched_at,
            ),
            "last_attempt_at": insert_statement.excluded.last_attempt_at,
            "updated_at": now,
        },
    ).returning(DocumentLocalization.id)
    return int(session.execute(statement).scalar_one())
