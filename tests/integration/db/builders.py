"""### Canonical builders shared by the database integration tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from lexcrawl.actors.base import ActorRunResult
from lexcrawl.models.document import (
    CanonicalDocument,
    DocumentResource,
    DocumentTranslation,
    compute_content_hash,
    ensure_text_marker,
)

SITE = "https://listing.example.test"


def marked(text: str) -> tuple[str, str]:
    """### Return marked text and its canonical hash."""
    body = ensure_text_marker(text)
    return body, compute_content_hash(body)


def resource(
    *,
    title: str = "attachment.pdf",
    url: str | None = f"{SITE}/files/attachment.pdf",
    kind: str = "pdf",
    status: str | None = "complete",
    text: str | None = "pdf body",
    native_id: str | None = None,
    error: str | None = None,
    mime_type: str | None = "application/pdf",
    file_size: int | None = 1024,
) -> DocumentResource:
    """### Build one canonical resource."""
    body, digest = marked(text) if text is not None and status == "complete" else (None, None)
    if status == "url_unavailable":
        url = None
        body, digest = None, None
    payload: dict[str, Any] = {
        "title": title,
        "native_id": native_id,
        "url": url,
        "kind": kind,
        "text": body,
        "content_hash": digest,
        "status": status,
        "error": error,
        "mime_type": mime_type,
        "file_size": file_size,
    }
    return DocumentResource.model_validate(payload)


def translation(
    *,
    language: str = "en",
    url: str = f"{SITE}/en/news/one",
    title: str = "English title",
    text: str | None = "english body",
    status: str = "complete",
    error: str | None = None,
) -> DocumentTranslation:
    """### Build one canonical translation."""
    body, digest = marked(text) if text is not None and status == "complete" else (None, None)
    return DocumentTranslation.model_validate(
        {
            "language": language,
            "source_url": url,
            "title": title,
            "text": body,
            "content_hash": digest,
            "status": status,
            "error": error,
        }
    )


def document(
    *,
    source: str = "uae_legislation_news",
    url: str = f"{SITE}/ar/news/one",
    native_id: str | None = None,
    title: str = "عنوان الخبر",
    language: str = "ar",
    text: str | None = "الفقرة الأولى",
    status: str | None = "complete",
    translations: tuple[DocumentTranslation, ...] = (),
    resources: tuple[DocumentResource, ...] = (),
    published_at: date | None = date(2026, 1, 8),
    published_month: int | None = None,
    published_year: int | None = None,
    published_precision: str | None = None,
    type_of_law: str | None = None,
    extraction_error: str | None = None,
    topics: tuple[str, ...] = (),
) -> CanonicalDocument:
    """### Build one canonical document."""
    body, digest = marked(text) if text is not None else (None, None)
    return CanonicalDocument.model_validate(
        {
            "source": source,
            "source_url": url,
            "native_id": native_id,
            "title": title,
            "language": language,
            "text": body,
            "content_hash": digest,
            "extraction_status": status,
            "extraction_error": extraction_error,
            "published_at": published_at,
            "published_month": published_month,
            "published_year": published_year,
            "published_precision": published_precision,
            "type_of_law": type_of_law,
            "topics": topics,
            "translations": translations,
            "resources": resources,
        }
    )


def result(
    *,
    source: str = "uae_legislation_news",
    documents: tuple[CanonicalDocument, ...] = (),
    errors: tuple[str, ...] = (),
    **counters: Any,
) -> ActorRunResult:
    """### Build one actor run result with explicit counters."""
    payload = {
        "source": source,
        "documents": list(documents),
        "errors": list(errors),
        "pagination_complete": True,
        "duration_seconds": 1.0,
        **counters,
    }
    return ActorRunResult(**payload)


def timestamp() -> datetime:
    """### Return a timezone-aware instant for direct model inserts."""
    return datetime.now(tz=UTC)
