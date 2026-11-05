"""### Database queries for linked resources and attachments."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from lexcrawl.api.schemas.common import ContentKind
from lexcrawl.api.services.content_queries import classify_content, primary_localization_id
from lexcrawl.db.models import Document, DocumentLocalization, DocumentResource, Source

ResourceRow = tuple[DocumentResource, Document, Source, DocumentLocalization, ContentKind]


@dataclass(frozen=True, slots=True)
class ResourcePage:
    """### One page of resource rows with their parent content and the total match count."""

    rows: list[ResourceRow]
    total: int


def _base_statement() -> Select[tuple[DocumentResource, Document, Source, DocumentLocalization]]:
    """### Build the resource query joined to its parent document, source, and primary localization."""
    return (
        select(DocumentResource, Document, Source, DocumentLocalization)
        .join(Document, DocumentResource.document_id == Document.id)
        .join(Source, Document.source_id == Source.id)
        .join(DocumentLocalization, DocumentLocalization.id == primary_localization_id())
    )


def list_resources(
    session: Session,
    *,
    document_id: int | None = None,
    resource_type: str | None = None,
    extraction_status: str | None = None,
    limit: int,
    offset: int,
) -> ResourcePage:
    """### Return one paginated page of resources with their parent identity."""
    statement = _base_statement()
    if document_id is not None:
        statement = statement.where(DocumentResource.document_id == document_id)
    if resource_type is not None:
        statement = statement.where(DocumentResource.resource_type == resource_type)
    if extraction_status is not None:
        statement = statement.where(DocumentResource.extraction_status == extraction_status)
    total = int(session.execute(select(func.count()).select_from(statement.subquery())).scalar_one())
    ordered = (
        statement.order_by(DocumentResource.updated_at.desc(), DocumentResource.id.desc()).limit(limit).offset(offset)
    )
    rows: list[ResourceRow] = []
    for resource, document, source, localization in session.execute(ordered).all():
        kind = classify_content(source.code, document.document_type) or "article"
        rows.append((resource, document, source, localization, kind))
    return ResourcePage(rows=rows, total=total)


def get_resource(
    session: Session,
    resource_id: int,
) -> tuple[DocumentResource, Document, Source, DocumentLocalization, ContentKind] | None:
    """### Return one resource with its parent content and primary localization, or None."""
    statement = _base_statement().where(DocumentResource.id == resource_id)
    row = session.execute(statement).first()
    if row is None:
        return None
    resource, document, source, localization = row
    kind = classify_content(source.code, document.document_type) or "article"
    return resource, document, source, localization, kind
