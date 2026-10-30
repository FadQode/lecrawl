"""### Canonical document contract shared by actors, pipeline, and persistence."""

from __future__ import annotations

import hashlib
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

TEXT_BEGIN_MARKER = "TEXT-BEGIN"


class ArticleContentError(ValueError):
    """### Raised when a source page lacks the content the contract requires."""


def ensure_text_marker(text: str) -> str:
    """### Prepend `TEXT-BEGIN` to collected text at most once."""
    body = text.strip()
    if body.startswith(TEXT_BEGIN_MARKER):
        return body
    return f"{TEXT_BEGIN_MARKER}\n{body}"


class ArticleStub(BaseModel):
    """### Listing-page summary of one article, before detail fetching."""

    model_config = ConfigDict(frozen=True)

    url: str
    title: str
    published_at: date | None = None
    summary: str | None = None
    image_url: str | None = None


class DocumentResource(BaseModel):
    """### One linked resource attached to a collected document."""

    model_config = ConfigDict(frozen=True)

    title: str
    url: str
    kind: str = "other"
    text: str | None = None
    error: str | None = None

    @field_validator("text")
    @classmethod
    def _mark_collected_text(cls, value: str | None) -> str | None:
        """### Ensure extracted resource text carries the required marker."""
        if value is None or not value.strip():
            return None
        return ensure_text_marker(value)


class RawArticle(BaseModel):
    """### Actor output for one article, before canonicalization."""

    source: str
    url: str
    title: str
    language: str
    published_at: date | None = None
    summary: str | None = None
    image_url: str | None = None
    type_of_law: str | None = None
    topics: tuple[str, ...] = ()
    resources: tuple[DocumentResource, ...] = ()
    paragraphs: list[str] = Field(default_factory=list)

    @field_validator("title", "url")
    @classmethod
    def _require_non_empty(cls, value: str) -> str:
        """### Reject blank required identity fields."""
        if not value.strip():
            raise ValueError("value must not be blank")
        return value.strip()

    @property
    def text(self) -> str:
        """### Join extracted paragraphs in their original order."""
        return "\n\n".join(paragraph for paragraph in self.paragraphs if paragraph.strip())


class CanonicalDocument(BaseModel):
    """### Stored representation of one collected document."""

    model_config = ConfigDict(frozen=True)

    source: str
    source_url: str
    title: str
    language: str
    text: str | None = None
    content_hash: str | None = None
    published_at: date | None = None
    summary: str | None = None
    image_url: str | None = None
    type_of_law: str | None = None
    topics: tuple[str, ...] = ()
    resources: tuple[DocumentResource, ...] = ()

    @classmethod
    def from_raw(cls, raw: RawArticle) -> CanonicalDocument:
        """### Canonicalize a raw article, applying the required text marker."""
        body = ensure_text_marker(raw.text) if raw.text.strip() else None
        return cls(
            source=raw.source,
            source_url=raw.url,
            title=raw.title,
            language=raw.language,
            text=body,
            content_hash=hashlib.sha256(body.encode("utf-8")).hexdigest() if body is not None else None,
            published_at=raw.published_at,
            summary=raw.summary,
            image_url=raw.image_url,
            type_of_law=raw.type_of_law,
            topics=raw.topics,
            resources=raw.resources,
        )

    @classmethod
    def from_listing_entry(
        cls,
        entry: ArticleStub,
        *,
        source: str,
        language: str,
        type_of_law: str | None = None,
    ) -> CanonicalDocument:
        """### Store discovery metadata for an article whose text is not collected yet."""
        return cls(
            source=source,
            source_url=entry.url,
            title=entry.title,
            language=language,
            published_at=entry.published_at,
            summary=entry.summary,
            image_url=entry.image_url,
            type_of_law=type_of_law,
        )
