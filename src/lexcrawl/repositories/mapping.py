"""### Pure mapping between canonical models and persistence column values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lexcrawl.actors.base import ActorRunResult
from lexcrawl.models.document import CanonicalDocument, DocumentResource, DocumentTranslation

# $ document_type falls back to a per-source classification when the source sets none.$
DEFAULT_DOCUMENT_TYPES: dict[str, str] = {
    "uae_legislation_news": "news_article",
    "cba_news_releases": "news_release",
    "commerce_reports": "report",
}
FALLBACK_DOCUMENT_TYPE = "document"

# $ Stage and type for run-level strings, which carry no structured context.$
RUN_ERROR_STAGE = "unknown"
RUN_ERROR_TYPE = "actor_error"
DOCUMENT_ERROR_TYPE = "document_extraction_error"
TRANSLATION_ERROR_TYPE = "translation_error"
RESOURCE_ERROR_TYPE = "resource_extraction_error"
EXTRACTION_STAGE = "extract"
MAX_MESSAGE_LENGTH = 2000

# $ Actor error phrasing mapped to the existing crawl_errors.stage vocabulary.$
_RUN_ERROR_MARKERS: tuple[tuple[str, str, str], ...] = (
    ("change detection fetch failed", "fetch", "fetch_error"),
    ("listing fetch failed", "fetch", "fetch_error"),
    ("article fetch failed", "fetch", "fetch_error"),
    ("firecrawl listing failed", "fetch", "fetch_error"),
    ("materialization failed", "fetch", "fetch_error"),
    ("returned http", "fetch", "fetch_error"),
    ("fetch failed", "fetch", "fetch_error"),
    ("listing parse failed", "parse", "parse_error"),
    ("article parse failed", "parse", "parse_error"),
    ("did not contain the report table", "parse", "parse_error"),
    ("no report table", "parse", "parse_error"),
    ("unusable listing record", "parse", "parse_error"),
    ("pagination loop detected", "discovery", "discovery_error"),
    ("no article urls discovered", "discovery", "discovery_error"),
    ("no news releases matched", "discovery", "discovery_error"),
    ("listing range gap", "discovery", "discovery_error"),
    ("listing total changed", "discovery", "discovery_error"),
    ("were not found while paginating", "discovery", "discovery_error"),
    ("stopped after hitting", "discovery", "budget_error"),
    ("resource url unavailable", "extract", "resource_error"),
    ("resource failed at", "extract", "resource_error"),
    ("resource access_blocked at", "extract", "resource_error"),
    ("resource url_unavailable at", "extract", "resource_error"),
    ("pdf processing failed", "extract", "resource_error"),
    ("no_completed_localization", "extract", "extraction_error"),
    ("change detection requires persisted documents", "persistence", "persistence_error"),
    ("change detection requires the persistence layer", "persistence", "persistence_error"),
    ("could not load persisted identities", "persistence", "persistence_error"),
    ("could not load incomplete reports", "persistence", "persistence_error"),
)
_SECRET_PATTERNS = (
    (re.compile(r"(://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),
    (re.compile(r"(api_key=)[^&\s]+"), r"\1***"),
    (re.compile(r"(token=)[^&\s]+"), r"\1***"),
)


def redact_secrets(message: str) -> str:
    """### Remove credentials and keys from a message before storing it."""
    redacted = message
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted[:MAX_MESSAGE_LENGTH]


@dataclass(frozen=True, slots=True)
class LocalizationPayload:
    """### Column values for one localization row."""

    language_code: str
    title: str | None
    summary: str | None
    text_content: str | None
    content_hash: str | None
    status: str
    source_url: str | None
    listing_url: str | None
    published_at: Any
    image_url: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class ErrorPayload:
    """### Column values for one crawl error row."""

    stage: str
    error_type: str
    message: str
    retryable: bool | None = None


def published_precision(document: CanonicalDocument) -> str:
    """### Resolve the stored precision, treating an exact date as day precision."""
    if document.published_precision is not None:
        return document.published_precision
    return "day" if document.published_at is not None else "unknown"


def document_type(document: CanonicalDocument) -> str:
    """### Resolve the document classification, preferring the source label."""
    label = (document.type_of_law or "").strip()
    if label:
        return label
    return DEFAULT_DOCUMENT_TYPES.get(document.source, FALLBACK_DOCUMENT_TYPE)


def document_status(document: CanonicalDocument) -> str:
    """### Resolve the primary localization status without inventing content."""
    if document.extraction_status is not None:
        return document.extraction_status
    return "complete" if document.text is not None else "pending"


def initial_extraction_attempts(document: CanonicalDocument) -> int:
    """### Start the attempt counter at one for a document that is still incomplete."""
    return 0 if document_status(document) == "complete" else 1


def document_values(document: CanonicalDocument, *, now: datetime) -> dict[str, Any]:
    """### Build the insert/update values for one logical document row."""
    return {
        "identity_key": document.identity_key,
        "native_id": document.native_id,
        "source_url": document.source_url,
        "listing_url": document.listing_url,
        "document_type": document_type(document),
        "published_at": document.published_at,
        "published_year": document.published_year,
        "published_month": document.published_month,
        "published_precision": published_precision(document),
        "published_date_raw": document.published_date_raw,
        "topics": list(document.topics),
        "image_url": document.image_url,
        "source_created_at": document.source_created_at,
        "provenance": document.provenance,
        "archive_url": document.archive_url,
        "archived_at": document.archived_at,
        "archive_listing_url": document.archive_listing_url,
        "extraction_status": document.extraction_status,
        "extraction_error": document.extraction_error,
        "extraction_attempts": initial_extraction_attempts(document),
        "first_seen_at": now,
        "last_seen_at": now,
        "created_at": now,
        "updated_at": now,
    }


def localization_payloads(document: CanonicalDocument) -> list[LocalizationPayload]:
    """### Build the primary localization followed by every translation."""
    payloads = [
        LocalizationPayload(
            language_code=document.language,
            title=document.title,
            summary=document.summary,
            text_content=document.text,
            content_hash=document.content_hash,
            status=document_status(document),
            source_url=document.source_url,
            listing_url=document.listing_url,
            published_at=document.published_at,
            image_url=document.image_url,
            error=document.extraction_error,
        )
    ]
    payloads.extend(_translation_payload(translation) for translation in document.translations)
    return payloads


def _translation_payload(translation: DocumentTranslation) -> LocalizationPayload:
    """### Build the row values for one translation."""

    return LocalizationPayload(
        language_code=translation.language,
        title=translation.title,
        summary=translation.summary,
        text_content=translation.text,
        content_hash=translation.content_hash,
        status=translation.status,
        source_url=translation.source_url,
        listing_url=translation.listing_url,
        published_at=translation.published_at,
        image_url=translation.image_url,
        error=translation.error,
    )


def resource_values(resource: DocumentResource, *, now: datetime) -> dict[str, Any]:
    """### Build the insert/update values for one resource row."""
    if not resource.identity_key:
        raise ValueError("canonical resources must carry an identity key")
    return {
        "identity_key": resource.identity_key,
        "native_id": resource.native_id,
        "title": resource.title,
        "display_title": resource.display_title,
        "original_url": resource.url,
        "resource_type": resource.kind,
        "mime_type": resource.mime_type,
        "file_size_bytes": resource.file_size,
        "page_count": resource.page_count,
        "character_count": resource.character_count,
        "extracted_text": resource.text,
        "content_hash": resource.content_hash,
        "extraction_status": resource.status or "pending",
        "extraction_error": resource.error,
        "archive_url": resource.archive_url,
        "archived_at": resource.archived_at,
        "first_seen_at": now,
        "last_seen_at": now,
        "created_at": now,
        "updated_at": now,
    }


def run_counters(result: ActorRunResult) -> dict[str, int]:
    """### Return every counter persisted for one source run.

    `documents_processed` counts canonical documents emitted for the source.
    `documents_failed` mirrors the document-level extraction failures
    (`detail_pages_failed`), whose unit differs per source and is documented there.
    """
    return {
        "listing_pages_attempted": result.listing_pages_attempted,
        "listing_pages_succeeded": result.listing_pages_succeeded,
        "listing_pages_failed": result.listing_pages_failed,
        "articles_discovered": result.articles_discovered,
        "article_urls_deduplicated": result.article_urls_deduplicated,
        "records_filtered_out": result.records_filtered_out,
        "detail_pages_attempted": result.detail_pages_attempted,
        "detail_pages_succeeded": result.detail_pages_succeeded,
        "detail_pages_failed": result.detail_pages_failed,
        "records_missing_required_fields": result.records_missing_required_fields,
        "records_without_text": result.records_without_text,
        "resources_discovered": result.resources_discovered,
        "resources_processed": result.resources_processed,
        "resources_extracted": result.resources_extracted,
        "resources_unavailable": result.resources_unavailable,
        "resources_failed": result.resources_failed,
        "resources_skipped": result.resources_skipped,
        "documents_processed": len(result.documents),
        "documents_failed": result.detail_pages_failed,
        "documents_pending": result.documents_pending,
        "provider_credits_used": result.provider_credits_used,
    }


def classify_run_error(message: str) -> tuple[str, str]:
    """### Map an actor error string to the closest existing stage and category."""
    lowered = message.lower()
    for marker, stage, error_type in _RUN_ERROR_MARKERS:
        if marker in lowered:
            return stage, error_type
    return RUN_ERROR_STAGE, RUN_ERROR_TYPE


def run_error_payloads(result: ActorRunResult) -> list[ErrorPayload]:
    """### Convert run-level error strings into classified, redacted error rows."""
    payloads: list[ErrorPayload] = []
    for message in result.errors:
        text = str(message)
        stage, error_type = classify_run_error(text)
        payloads.append(ErrorPayload(stage=stage, error_type=error_type, message=redact_secrets(text)))
    return payloads


def error_summary(result: ActorRunResult, *, limit: int = 4000) -> str | None:
    """### Build a bounded summary of the run's error messages."""
    if not result.errors:
        return None
    joined = "; ".join(str(message) for message in result.errors)
    return redact_secrets(joined)[:limit]
