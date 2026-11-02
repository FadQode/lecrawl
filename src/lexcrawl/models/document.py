"""### Canonical document contract shared by actors, pipeline, and persistence."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TEXT_BEGIN_MARKER = "TEXT-BEGIN"

# $ Extraction state shared by every linked resource, across sources.$
ResourceStatus = Literal["pending", "complete", "not_applicable", "access_blocked", "failed", "url_unavailable"]
ResourceKind = Literal["pdf", "excel", "word", "csv", "other"]
TranslationStatus = Literal[
    "not_configured", "not_discovered", "pending", "complete", "failed", "access_blocked", "unavailable"
]
DocumentStatus = Literal["pending", "complete", "failed", "access_blocked", "unavailable"]

_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")

# $ How precisely a source states a publication date.$
PublicationPrecision = Literal["day", "month", "year"]

# $ Where a record was obtained: the live source or a public archive.$
Provenance = Literal["live", "wayback"]


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
    normalized = urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, "")
    )
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
    native_id: str | None = None
    identity_key: str | None = None
    parent_identity: str | None = None
    url: str | None = None
    kind: ResourceKind = "other"
    text: str | None = None
    error: str | None = None
    # $ Optional enrichment keeps the canonical resource usable for every source.$
    display_title: str | None = None
    mime_type: str | None = None
    file_size: int | None = None
    page_count: int | None = Field(default=None, ge=0)
    character_count: int | None = Field(default=None, ge=0)
    content_hash: str | None = None
    status: ResourceStatus | None = None
    archive_url: str | None = None
    archived_at: datetime | None = None

    @field_validator("text")
    @classmethod
    def _mark_collected_text(cls, value: str | None) -> str | None:
        """### Ensure extracted resource text carries the required marker."""
        if value is None or not value.strip():
            return None
        return ensure_text_marker(value)

    @model_validator(mode="after")
    def _check_resource_contract(self) -> DocumentResource:
        """### Enforce canonical resource status, text, hash and count consistency."""
        if self.status == "complete":
            if self.text is None or self.content_hash is None:
                raise ValueError("complete resources require text and content hash")
        elif self.text is not None or self.content_hash is not None:
            raise ValueError("non-complete resources must not carry text or content hash")
        if self.text is not None and self.content_hash != compute_content_hash(self.text):
            raise ValueError("resource content hash does not match text")
        if self.text is None and self.content_hash is not None:
            raise ValueError("resource content hash requires text")
        if self.text is not None and self.character_count is not None and self.character_count != len(self.text):
            raise ValueError("resource character count does not match text")
        return self


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


class DocumentTranslation(BaseModel):
    """### One additional language variant of a collected document."""

    model_config = ConfigDict(frozen=True)

    language: str
    source_url: str | None = None
    title: str | None = None
    text: str | None = None
    content_hash: str | None = None
    status: TranslationStatus = "complete"
    error: str | None = None
    listing_url: str | None = None
    parent_identity: str | None = None
    published_at: date | None = None
    summary: str | None = None
    image_url: str | None = None

    @field_validator("text")
    @classmethod
    def _mark_collected_text(cls, value: str | None) -> str | None:
        """### Ensure translated text carries the required marker."""
        if value is None or not value.strip():
            return None
        return ensure_text_marker(value)

    @model_validator(mode="after")
    def _check_state(self) -> DocumentTranslation:
        """### Enforce the translation text and status contract."""
        if not _LANGUAGE_TAG.fullmatch(self.language):
            raise ValueError("translation language must be a valid language tag")
        if self.source_url is not None:
            url = urlsplit(self.source_url)
            if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
                raise ValueError("translation source URL must be an absolute HTTP(S) URL")
        if self.status in {"not_configured", "not_discovered"} and (
            self.source_url is not None or self.title is not None
        ):
            raise ValueError("unconfigured or undiscovered translations must not have source metadata")
        if self.status == "complete":
            if not self.source_url or not self.title or not self.text or not self.content_hash:
                raise ValueError("complete translations require URL, title, text, and hash")
            if compute_content_hash(self.text) != self.content_hash:
                raise ValueError("translation content hash does not match its text")
        elif self.text is not None or self.content_hash is not None:
            raise ValueError("incomplete translations must not carry text or hash")
        return self


class CanonicalDocument(BaseModel):
    """### Stored representation of one collected document."""

    model_config = ConfigDict(frozen=True)

    source: str
    native_id: str | None = None
    identity_key: str | None = None
    source_url: str
    listing_url: str | None = None
    title: str
    language: str
    extraction_status: DocumentStatus | None = None
    extraction_error: str | None = None
    text: str | None = None
    content_hash: str | None = None
    published_at: date | None = None
    # $ Month precision is preserved without inventing a day.$
    published_month: int | None = Field(default=None, ge=1, le=12)
    published_year: int | None = Field(default=None, ge=1800, le=2100)
    published_date_raw: str | None = None
    published_precision: PublicationPrecision | None = None
    source_created_at: datetime | None = None
    summary: str | None = None
    image_url: str | None = None
    type_of_law: str | None = None
    topics: tuple[str, ...] = ()
    resources: tuple[DocumentResource, ...] = ()
    translations: tuple[DocumentTranslation, ...] = ()
    provenance: Provenance = "live"
    archive_url: str | None = None
    archived_at: datetime | None = None
    archive_listing_url: str | None = None

    @model_validator(mode="after")
    def _check_canonical_contract(self) -> CanonicalDocument:
        """### Enforce shared identity, URL, text, hash, and date invariants."""
        if (
            not self.source.strip()
            or not self.source_url.strip()
            or not self.title.strip()
            or not self.language.strip()
        ):
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
        normalized_resources = []
        for index, resource in enumerate(self.resources):
            if resource.url is None and resource.status != "url_unavailable":
                raise ValueError("canonical resources without a URL must be marked url_unavailable")
            if resource.url is not None:
                resource_url = urlsplit(resource.url)
                if (
                    resource_url.scheme not in {"http", "https"}
                    or not resource_url.hostname
                    or resource_url.username
                    or resource_url.password
                ):
                    raise ValueError("canonical resource URL must be an absolute HTTP(S) URL")
            if resource.url:
                parts = urlsplit(resource.url)
                resource_token = urlunsplit(
                    (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, "")
                )
            else:
                resource_token = (
                    resource.native_id or resource.content_hash or f"{resource.kind}:{resource.title}:{index}"
                )
            if resource.native_id:
                resource_token = f"id:{resource.native_id}"
            resource_identity = f"{expected_identity}:resource:{resource_token}"
            if resource.identity_key is not None and resource.identity_key != resource_identity:
                raise ValueError("resource identity key does not match parent and source identity")
            normalized_resources.append(
                resource.model_copy(update={"identity_key": resource_identity, "parent_identity": expected_identity})
            )
        object.__setattr__(self, "resources", tuple(normalized_resources))
        normalized_translations = []
        seen_languages: set[str] = set()
        for translation in self.translations:
            if translation.language in seen_languages or (
                self.text is not None and translation.language == self.language
            ):
                raise ValueError("canonical translation languages must be unique and differ from a completed body")
            seen_languages.add(translation.language)
            if translation.parent_identity not in {None, expected_identity}:
                raise ValueError("translation parent identity does not match canonical document")
            normalized_translations.append(translation.model_copy(update={"parent_identity": expected_identity}))
        object.__setattr__(self, "translations", tuple(normalized_translations))
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
        if self.published_precision == "day" and self.published_at is None:
            raise ValueError("day precision requires published_at")
        if self.published_precision == "month" and (self.published_month is None or self.published_year is None):
            raise ValueError("month precision requires published_month and published_year")
        if self.published_precision == "year" and self.published_year is None:
            raise ValueError("year precision requires published_year")
        if self.published_month is not None and self.published_year is None:
            raise ValueError("published_month requires published_year")
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
            summary=entry.summary,
            image_url=entry.image_url,
            type_of_law=type_of_law,
        )
