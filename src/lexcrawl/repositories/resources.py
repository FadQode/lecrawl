"""### Resource repository: attachments and their extraction outcomes."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lexcrawl.db.models import DocumentResource
from lexcrawl.models.document import DocumentResource as CanonicalResource
from lexcrawl.repositories.mapping import resource_values


def upsert_resource(
    session: Session,
    *,
    document_id: int,
    resource: CanonicalResource,
    now: datetime,
) -> int:
    """### Insert or update one attachment, preserving previous extraction results."""
    values = resource_values(resource, now=now) | {"document_id": document_id}
    insert_statement = insert(DocumentResource).values(**values)
    statement = insert_statement.on_conflict_do_update(
        index_elements=[DocumentResource.document_id, DocumentResource.identity_key],
        set_={
            "native_id": func.coalesce(insert_statement.excluded.native_id, DocumentResource.native_id),
            "title": insert_statement.excluded.title,
            "display_title": func.coalesce(insert_statement.excluded.display_title, DocumentResource.display_title),
            "original_url": func.coalesce(insert_statement.excluded.original_url, DocumentResource.original_url),
            "resource_type": insert_statement.excluded.resource_type,
            "mime_type": func.coalesce(insert_statement.excluded.mime_type, DocumentResource.mime_type),
            "file_size_bytes": func.coalesce(
                insert_statement.excluded.file_size_bytes, DocumentResource.file_size_bytes
            ),
            "page_count": func.coalesce(insert_statement.excluded.page_count, DocumentResource.page_count),
            "character_count": func.coalesce(
                insert_statement.excluded.character_count, DocumentResource.character_count
            ),
            # $ A failed extraction keeps the last good text instead of erasing it.$
            "extracted_text": func.coalesce(insert_statement.excluded.extracted_text, DocumentResource.extracted_text),
            "content_hash": func.coalesce(insert_statement.excluded.content_hash, DocumentResource.content_hash),
            "extraction_status": insert_statement.excluded.extraction_status,
            "extraction_error": insert_statement.excluded.extraction_error,
            "archive_url": func.coalesce(insert_statement.excluded.archive_url, DocumentResource.archive_url),
            "archived_at": func.coalesce(insert_statement.excluded.archived_at, DocumentResource.archived_at),
            "last_seen_at": now,
            "updated_at": now,
        },
    ).returning(DocumentResource.id)
    return int(session.execute(statement).scalar_one())
