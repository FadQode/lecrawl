"""### Shared response schemas for the content API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from lexcrawl.db.models import DocumentLocalization

ContentKind = Literal["article", "report"]


class Pagination(BaseModel):
    """### Pagination metadata returned with every list response."""

    limit: int = Field(description="Maximum number of items requested for this page.")
    offset: int = Field(description="Number of items skipped before this page.")
    total: int = Field(description="Total number of items matching the query across all pages.")


class Page[T](BaseModel):
    """### A slice of a result set with pagination metadata."""

    items: list[T] = Field(description="The items on this page.")
    pagination: Pagination = Field(description="Pagination metadata for the result set.")


class ProblemDetail(BaseModel):
    """### RFC 9457 Problem Details body returned for errors."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "about:blank",
                "title": "Not Found",
                "status": 404,
                "detail": "article 999 was not found",
                "instance": "/api/v1/articles/999",
            }
        }
    )

    type: str = Field(default="about:blank", description="Problem type URI; 'about:blank' by default.")
    title: str = Field(description="Short, human-readable summary of the problem.")
    status: int = Field(description="HTTP status code.")
    detail: str | None = Field(default=None, description="Human-readable explanation of the problem.")
    instance: str | None = Field(default=None, description="URI reference identifying the specific occurrence.")


class LocalizationOut(BaseModel):
    """### One language variant of a content item."""

    language: str = Field(description="BCP-47 language tag, e.g. 'en' or 'ar'.")
    title: str | None = Field(default=None, description="Localized title.")
    summary: str | None = Field(default=None, description="Localized summary, when the source provides one.")
    status: str = Field(description="Localization status: pending, complete, failed, access_blocked, or unavailable.")
    has_content: bool = Field(description="Whether extracted text is stored for this localization.")
    content_length: int | None = Field(default=None, description="Length of the extracted text in characters.")
    content_hash: str | None = Field(default=None, description="SHA-256 hex digest of the marked extracted text.")
    source_url: str | None = Field(default=None, description="Source page URL for this localization.")
    published_at: date | None = Field(default=None, description="Publication date (YYYY-MM-DD) when known.")
    fetched_at: datetime | None = Field(default=None, description="When the content was last successfully fetched.")
    last_attempt_at: datetime | None = Field(default=None, description="When extraction was last attempted.")
    error: str | None = Field(default=None, description="Last extraction error, if any.")

    @classmethod
    def from_model(cls, localization: DocumentLocalization) -> LocalizationOut:
        """### Map a localization row to its public representation."""
        text = localization.text_content
        return cls(
            language=localization.language_code,
            title=localization.title,
            summary=localization.summary,
            status=localization.status,
            has_content=text is not None,
            content_length=len(text) if text is not None else None,
            content_hash=localization.content_hash,
            source_url=localization.source_url,
            published_at=localization.published_at,
            fetched_at=localization.fetched_at,
            last_attempt_at=localization.last_attempt_at,
            error=localization.error,
        )
