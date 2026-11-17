"""### Document query endpoints (list/detail/resources/text)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.api.auth import require_bearer_token
from lexcrawl.api.contracts import (
    DocumentDetailModel,
    DocumentListItemModel,
    DocumentPageModel,
    DocumentTextModel,
    ResourceModel,
)
from lexcrawl.api.dependencies import get_factory
from lexcrawl.db.session import session_scope
from lexcrawl.repositories import reads

router = APIRouter(prefix="/documents", tags=["documents"], dependencies=[Depends(require_bearer_token)])


@router.get("", response_model=DocumentPageModel)
def list_documents(
    source: str | None = None,
    status: str | None = None,
    document_type: str | None = None,
    topic: str | None = None,
    published_from: date | None = None,
    published_to: date | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    factory: sessionmaker[Session] = Depends(get_factory),
) -> DocumentPageModel:
    """### List document metadata (never extracted text) with keyset pagination."""
    filters = reads.DocumentFilters(
        source_code=source,
        extraction_status=status,
        document_type=document_type,
        topic=topic,
        published_from=published_from,
        published_to=published_to,
    )
    with session_scope(factory) as session:
        try:
            page = reads.list_documents(session, filters, cursor=cursor, limit=limit)
        except reads.InvalidCursorError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    return DocumentPageModel(
        items=[DocumentListItemModel.model_validate(item) for item in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.get("/{document_id}", response_model=DocumentDetailModel)
def get_document(document_id: int, factory: sessionmaker[Session] = Depends(get_factory)) -> DocumentDetailModel:
    """### Return a document including its extracted text."""
    with session_scope(factory) as session:
        detail = reads.get_document(session, document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="document not found")
    return DocumentDetailModel.model_validate(detail)


@router.get("/{document_id}/resources", response_model=list[ResourceModel])
def get_resources(document_id: int, factory: sessionmaker[Session] = Depends(get_factory)) -> list[ResourceModel]:
    """### Return a document's attachments."""
    with session_scope(factory) as session:
        if reads.get_document(session, document_id) is None:
            raise HTTPException(status_code=404, detail="document not found")
        items = reads.list_resources(session, document_id)
    return [ResourceModel.model_validate(item) for item in items]


@router.get("/{document_id}/text", response_model=DocumentTextModel)
def get_text(document_id: int, factory: sessionmaker[Session] = Depends(get_factory)) -> DocumentTextModel:
    """### Return only a document's extracted text and hash."""
    with session_scope(factory) as session:
        detail = reads.get_document(session, document_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="document not found")
    return DocumentTextModel(document_id=detail.id, text=detail.text, content_hash=detail.content_hash)
