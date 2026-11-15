"""### Canonical document contract shared by actors, pipeline, and persistence."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TEXT_BEGIN_MARKER = "TEXT-BEGIN"

# $ How far a document's text extraction got, shared by every source.$
DocumentStatus = Literal["pending", "complete", "failed", "access_blocked", "unavailable"]

# $ Extraction state shared by every linked resource, across sources.$
ResourceKind = Literal["pdf", "excel", "word", "csv", "other"]
ResourceStatus = Literal["pending", "complete", "not_applicable", "access_blocked", "failed", "url_unavailable"]

# $ How precisely a source states a publication date.$
PublicationPrecision = Literal["day", "month", "year"]

MIN_YEAR = 1800
MAX_YEAR = 2100

_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")


def _check_date_precision(
    *,
    published_at: date | None,
    published_month: int | None,
    published_year: int | None,
    published_precision: PublicationPrecision | None,
) -> None:
    """### Enforce the publication-date precision contract shared by documents."""
    if published_precision == "day" and published_at is None:
        raise ValueError("day precision requires published_at")
    if published_precision == "month" and (published_month is None or published_year is None):
        raise ValueError("month precision requires published_month and published_year")
    if published_precision == "year" and published_year is None:
        raise ValueError("year precision requires published_year")
    if published_month is not None and published_year is None:
        raise ValueError("published_month requires published_year")


class ArticleContentError(ValueError):
    """### Raised when a source page lacks the content the contract requires."""


def compute_content_hash(text: str) -> str:
    """### Return the canonical content hash for one extracted text document."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def ensure_text_marker(text: str) -> str:
    """### Prepend `TEXT-BEGIN` to collected text at most once."""
    body = text.strip()
    if body.startswith(TEXT_BEGIN_MARKER):
        return body
    return f"{TEXT_BEGIN_MARKER}\n{body}"


def stable_identity(source: str, native_id: str | None, source_url: str) -> str:
    """### Build a deterministic source-scoped identity without using content."""
    if native_id and native_id.strip():
        return f"{source}:id:{native_id.strip()}"
    parts = urlsplit(source_url)
    # $ Normalize the host and trailing slash, drop query/fragment; keep path case as-is.$
    normalized = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", "", ""))
    return f"{source}:url:{normalized}"


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
    url: str | None = None
    kind: ResourceKind = "other"
    status: ResourceStatus = "pending"
    text: str | None = None
    content_hash: str | None = None
    error: str | None = None

    @field_validator("text")
    @classmethod
    def _mark_collected_text(cls, value: str | None) -> str | None:
        """### Ensure extracted resource text carries the required marker."""
        if value is None or not value.strip():
            return None
        return ensure_text_marker(value)

    @model_validator(mode="after")
    def _check_resource_contract(self) -> DocumentResource:
        """### Enforce resource status, URL, text, and hash consistency."""
        if self.url is None and self.status != "url_unavailable":
            raise ValueError("a resource without a URL must be marked url_unavailable")
        if self.status == "complete":
            if self.text is None or self.content_hash is None:
                raise ValueError("complete resources require text and content hash")
            if compute_content_hash(self.text) != self.content_hash:
                raise ValueError("resource content hash does not match text")
        elif self.text is not None or self.content_hash is not None:
            raise ValueError("non-complete resources must not carry text or content hash")
        return self


class RawArticle(BaseModel):
    """### Actor output for one article, before canonicalization."""

    source: str
    url: str
    title: str
    language: str
    published_at: date | None = None
    published_month: int | None = Field(default=None, ge=1, le=12)
    published_year: int | None = Field(default=None, ge=MIN_YEAR, le=MAX_YEAR)
    published_date_raw: str | None = None
    published_precision: PublicationPrecision | None = None
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
    native_id: str | None = None
    identity_key: str | None = None
    source_url: str
    title: str
    language: str
    extraction_status: DocumentStatus | None = None
    extraction_error: str | None = None
    text: str | None = None
    content_hash: str | None = None
    published_at: date | None = None
    published_month: int | None = Field(default=None, ge=1, le=12)
    published_year: int | None = Field(default=None, ge=MIN_YEAR, le=MAX_YEAR)
    published_date_raw: str | None = None
    published_precision: PublicationPrecision | None = None
    summary: str | None = None
    image_url: str | None = None
    type_of_law: str | None = None
    topics: tuple[str, ...] = ()
    resources: tuple[DocumentResource, ...] = ()

    @model_validator(mode="after")
    def _check_canonical_contract(self) -> CanonicalDocument:
        """### Enforce shared identity, URL, text, and hash invariants."""
        required = (self.source, self.source_url, self.title, self.language)
        if not all(value.strip() for value in required):
            raise ValueError("canonical source, URL, title, and language must not be blank")
        if not _LANGUAGE_TAG.fullmatch(self.language):
            raise ValueError("canonical language must be a valid language tag")
        url = urlsplit(self.source_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError("canonical source URL must be an absolute HTTP(S) URL")
        expected_identity = stable_identity(self.source, self.native_id, self.source_url)
        if self.identity_key is not None and self.identity_key != expected_identity:
            raise ValueError("canonical identity key does not match source identity")
        object.__setattr__(self, "identity_key", expected_identity)
        if self.text is None:
            if self.content_hash is not None:
                raise ValueError("canonical hash requires text")
            if self.extraction_status == "complete":
                raise ValueError("complete canonical documents require text")
        else:
            marked = ensure_text_marker(self.text)
            object.__setattr__(self, "text", marked)
            if not self.content_hash or compute_content_hash(marked) != self.content_hash:
                raise ValueError("canonical content hash does not match text")
            if self.extraction_status not in {None, "complete"}:
                raise ValueError("incomplete canonical documents must not carry text")
        _check_date_precision(
            published_at=self.published_at,
            published_month=self.published_month,
            published_year=self.published_year,
            published_precision=self.published_precision,
        )
        return self

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
            content_hash=compute_content_hash(body) if body is not None else None,
            extraction_status="complete" if body is not None else "pending",
            published_at=raw.published_at,
            published_month=raw.published_month,
            published_year=raw.published_year,
            published_date_raw=raw.published_date_raw,
            published_precision=raw.published_precision,
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
        native_id: str | None = None,
        extraction_status: DocumentStatus = "pending",
        extraction_error: str | None = None,
        published_month: int | None = None,
        published_year: int | None = None,
        published_date_raw: str | None = None,
        published_precision: PublicationPrecision | None = None,
        resources: tuple[DocumentResource, ...] = (),
    ) -> CanonicalDocument:
        """### Store discovery metadata for an article whose text is not collected yet."""
        return cls(
            source=source,
            native_id=native_id,
            source_url=entry.url,
            title=entry.title,
            language=language,
            extraction_status=extraction_status,
            extraction_error=extraction_error,
            published_at=entry.published_at,
            published_month=published_month,
            published_year=published_year,
            published_date_raw=published_date_raw,
            published_precision=published_precision,
            summary=entry.summary,
            image_url=entry.image_url,
            type_of_law=type_of_law,
            resources=resources,
        )
