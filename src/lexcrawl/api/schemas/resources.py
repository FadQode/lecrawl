"""### Response schemas for linked resources and attachments."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from lexcrawl.api.schemas.common import ContentKind
from lexcrawl.db.models import Document, DocumentResource, Source


class ResourceParent(BaseModel):
    """### The content item a resource belongs to."""

    id: int = Field(description="Identifier of the parent content item.")
    kind: ContentKind = Field(description="Parent content kind: 'article' or 'report'.")
    source: str = Field(description="Source code of the parent content item.")
    title: str | None = Field(default=None, description="Parent content title.")
    url: str = Field(description="Canonical source URL of the parent content item.")

    @classmethod
    def from_models(
        cls,
        document: Document,
        source: Source,
        kind: ContentKind,
        title: str | None,
    ) -> ResourceParent:
        """### Build the parent reference for a resource."""
        return cls(id=document.id, kind=kind, source=source.code, title=title, url=document.source_url)


class ResourceOut(BaseModel):
    """### Public representation of a linked resource or attachment.

    A resource is metadata plus, when extraction succeeded, its extracted text. The API does not
    offer file download; `url` is the original remote location, not a local copy.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "document_id": 1,
                "parent": {
                    "id": 1,
                    "kind": "report",
                    "source": "commerce_reports",
                    "title": "Example report",
                    "url": "https://www.commerce.gov/data-and-reports/reports/example",
                },
                "identity_key": "commerce_reports:id:example:resource:https://www.commerce.gov/example.pdf",
                "title": "example.pdf",
                "display_title": "Example report (PDF)",
                "url": "https://www.commerce.gov/example.pdf",
                "resource_type": "pdf",
                "mime_type": "application/pdf",
                "file_size_bytes": 1024,
                "page_count": None,
                "character_count": None,
                "extraction_status": "access_blocked",
                "has_content": False,
                "content_hash": None,
                "extraction_error": "source refused the request (HTTP 403)",
                "first_seen_at": "2025-11-05T08:00:00Z",
                "last_seen_at": "2025-11-05T08:00:00Z",
            }
        }
    )

    id: int = Field(description="Resource identifier.")
    document_id: int = Field(description="Identifier of the owning content item.")
    parent: ResourceParent | None = Field(default=None, description="Reference to the owning content item.")
    identity_key: str = Field(description="Stable, source-scoped resource identity.")
    title: str = Field(description="Resource title as discovered.")
    display_title: str | None = Field(default=None, description="Human-friendly display title, when available.")
    url: str | None = Field(default=None, description="Original remote URL of the file; null when unavailable.")
    resource_type: str = Field(description="Resource type: pdf, excel, word, csv, or other.")
    mime_type: str | None = Field(default=None, description="Reported media type, when available.")
    file_size_bytes: int | None = Field(default=None, description="File size in bytes, when known.")
    page_count: int | None = Field(default=None, description="Page count for extracted PDFs, when known.")
    character_count: int | None = Field(default=None, description="Extracted character count, when known.")
    extraction_status: str = Field(
        description="Extraction status: pending, complete, not_applicable, access_blocked, failed, or url_unavailable."
    )
    has_content: bool = Field(description="Whether extracted text is stored for this resource.")
    content_hash: str | None = Field(default=None, description="SHA-256 hex digest of the extracted text.")
    extraction_error: str | None = Field(default=None, description="Last extraction error, if any.")
    first_seen_at: datetime = Field(description="When the resource was first discovered.")
    last_seen_at: datetime = Field(description="When the resource was last seen.")

    @classmethod
    def from_model(
        cls,
        resource: DocumentResource,
        *,
        parent: ResourceParent | None = None,
    ) -> ResourceOut:
        """### Map a resource row to its public representation."""
        return cls(
            id=resource.id,
            document_id=resource.document_id,
            parent=parent,
            identity_key=resource.identity_key,
            title=resource.title,
            display_title=resource.display_title,
            url=resource.original_url,
            resource_type=resource.resource_type,
            mime_type=resource.mime_type,
            file_size_bytes=resource.file_size_bytes,
            page_count=resource.page_count,
            character_count=resource.character_count,
            extraction_status=resource.extraction_status,
            has_content=resource.extracted_text is not None,
            content_hash=resource.content_hash,
            extraction_error=resource.extraction_error,
            first_seen_at=resource.first_seen_at,
            last_seen_at=resource.last_seen_at,
        )
