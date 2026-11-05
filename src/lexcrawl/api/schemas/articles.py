"""### Response schemas for articles (news articles and news releases)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

from lexcrawl.api.schemas.common import LocalizationOut
from lexcrawl.api.schemas.resources import ResourceOut
from lexcrawl.db.models import Document, DocumentLocalization, DocumentResource, Source


def _summary_payload(
    document: Document,
    source: Source,
    localization: DocumentLocalization,
) -> dict[str, Any]:
    """### Build the shared summary fields from a document and its primary localization."""
    return {
        "id": document.id,
        "source": source.code,
        "source_name": source.name,
        "title": localization.title,
        "url": document.source_url,
        "language": localization.language_code,
        "document_type": document.document_type,
        "published_at": document.published_at,
        "published_precision": document.published_precision,
        "published_year": document.published_year,
        "published_month": document.published_month,
        "summary": localization.summary,
        "image_url": document.image_url,
        "topics": list(document.topics),
        "has_content": localization.text_content is not None,
        "content_hash": localization.content_hash,
        "first_seen_at": document.first_seen_at,
        "last_seen_at": document.last_seen_at,
        "updated_at": document.updated_at,
    }


class ArticleSummary(BaseModel):
    """### Public summary of a content item, without its extracted body."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": 1,
                "kind": "article",
                "source": "cba_news_releases",
                "source_name": "Canadian Bar Association",
                "title": "Gen Z financial literacy program expands its scam prevention training",
                "url": "https://cba.ca/article/gen-z-financial-literacy-program-expands-scam-prevention-training",
                "language": "en",
                "document_type": "News Releases",
                "published_at": "2025-11-03",
                "published_precision": "day",
                "published_year": None,
                "published_month": None,
                "summary": "The Your Money Students program updated to better assist young Canadians spot scams",
                "image_url": "https://cba.ca/Assets/CanadianBankersAssociation/Images/Articles/Banking_You/choosingRightBankAccount2025.png",
                "topics": ["#Fraud", "#Financial Literacy"],
                "has_content": True,
                "content_hash": "6b6be57ce6d7342cdbd55a4e32466096d8afbba797fe92f200cdf67e54ab7c10",
                "first_seen_at": "2025-11-05T08:00:00Z",
                "last_seen_at": "2025-11-05T08:00:00Z",
                "updated_at": "2025-11-05T08:00:00Z",
            }
        }
    )

    id: int = Field(description="Internal content identifier used by the detail and resource endpoints.")
    kind: Literal["article", "report"] = Field(default="article", description="Content kind: 'article' or 'report'.")
    source: str = Field(description="Source code, e.g. 'cba_news_releases'.")
    source_name: str = Field(description="Human-readable source name.")
    title: str | None = Field(default=None, description="Primary-language title.")
    url: str = Field(description="Canonical source URL of the content item.")
    language: str | None = Field(default=None, description="Primary localization language tag.")
    document_type: str = Field(description="Source-specific content label, e.g. 'News Releases' or 'report'.")
    published_at: date | None = Field(default=None, description="Publication date, when known exactly (YYYY-MM-DD).")
    published_precision: str = Field(description="Publication precision: day, month, year, or unknown.")
    published_year: int | None = Field(default=None, description="Publication year for coarse precision.")
    published_month: int | None = Field(default=None, description="Publication month (1-12) for month precision.")
    summary: str | None = Field(default=None, description="Short summary, when available.")
    image_url: str | None = Field(default=None, description="Lead image URL, when available.")
    topics: list[str] = Field(default_factory=list, description="Topic tags extracted from the source.")
    has_content: bool = Field(description="Whether extracted body text is stored.")
    content_hash: str | None = Field(default=None, description="SHA-256 hex digest of the marked extracted text.")
    first_seen_at: datetime = Field(description="When the item was first discovered.")
    last_seen_at: datetime = Field(description="When the item was last seen during a crawl.")
    updated_at: datetime = Field(description="When the stored row was last updated.")

    @classmethod
    def from_row(
        cls,
        document: Document,
        source: Source,
        localization: DocumentLocalization,
    ) -> Self:
        """### Map a document row and its primary localization to a summary."""
        return cls(**_summary_payload(document, source, localization))


class ArticleDetail(ArticleSummary):
    """### Full public representation of a content item and its body."""

    native_id: str | None = Field(default=None, description="Source-native identifier, when the source provides one.")
    listing_url: str | None = Field(default=None, description="Listing page the item was discovered from.")
    content: str | None = Field(
        default=None,
        description="Extracted primary-language body text, beginning with the TEXT-BEGIN marker.",
    )
    localizations: list[LocalizationOut] = Field(description="Every stored language variant, primary first.")
    resources: list[ResourceOut] = Field(description="Linked resources (attachments) for this item.")

    @classmethod
    def from_rows(
        cls,
        document: Document,
        source: Source,
        localizations: list[DocumentLocalization],
        resources: list[DocumentResource],
    ) -> Self:
        """### Map a document row with its localizations and resources to a detail."""
        ordered = sorted(localizations, key=lambda item: item.id)
        primary = ordered[0]
        return cls(
            **_summary_payload(document, source, primary),
            native_id=document.native_id,
            listing_url=document.listing_url,
            content=primary.text_content,
            localizations=[LocalizationOut.from_model(item) for item in ordered],
            resources=[ResourceOut.from_model(item) for item in sorted(resources, key=lambda item: item.id)],
        )
