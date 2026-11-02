"""### Contract tests for the canonical document and its text marker."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from lexcrawl.models.document import (
    TEXT_BEGIN_MARKER,
    ArticleStub,
    CanonicalDocument,
    DocumentResource,
    RawArticle,
    compute_content_hash,
    ensure_text_marker,
)


def build_raw(**overrides: object) -> RawArticle:
    """### Build a representative raw article for contract tests."""
    payload: dict[str, object] = {
        "source": "uae_legislation_news",
        "url": "https://listing.example.test/ar/news/example",
        "title": "عنوان تجريبي",
        "language": "ar",
        "published_at": date(2026, 1, 8),
        "paragraphs": ["الفقرة الأولى.", "الفقرة الثانية."],
    }
    payload.update(overrides)
    return RawArticle.model_validate(payload)


def test_text_marker_is_prepended_once() -> None:
    """### The marker is placed at the start of collected text exactly once."""
    marked = ensure_text_marker("نص المقال")
    assert marked.startswith(TEXT_BEGIN_MARKER)
    assert marked.count(TEXT_BEGIN_MARKER) == 1


def test_text_marker_is_idempotent() -> None:
    """### Re-processing already marked text does not duplicate the marker."""
    once = ensure_text_marker("نص المقال")
    twice = ensure_text_marker(once)
    assert twice == once
    assert twice.count(TEXT_BEGIN_MARKER) == 1


def test_canonical_document_starts_with_marker_and_hashes_content() -> None:
    """### A canonical document exposes marked text and a stable content hash."""
    document = CanonicalDocument.from_raw(build_raw())
    assert document.text.startswith(TEXT_BEGIN_MARKER)
    assert document.content_hash == CanonicalDocument.from_raw(build_raw()).content_hash
    assert document.text.count(TEXT_BEGIN_MARKER) == 1


def test_canonical_document_text_joins_paragraphs_in_order() -> None:
    """### Extracted paragraphs keep their original order in the stored text."""
    document = CanonicalDocument.from_raw(build_raw())
    assert document.text.endswith("الفقرة الأولى.\n\nالفقرة الثانية.")
    assert document.published_at == date(2026, 1, 8)
    assert document.source_url == "https://listing.example.test/ar/news/example"


def test_raw_article_rejects_blank_required_fields() -> None:
    """### Blank titles and URLs are rejected by the contract."""
    with pytest.raises(ValidationError):
        build_raw(title="   ")
    with pytest.raises(ValidationError):
        build_raw(url="")


def test_article_stub_defaults_optional_fields() -> None:
    """### Listing stubs allow absent optional metadata."""
    stub = ArticleStub(url="https://listing.example.test/ar/news/example", title="عنوان")
    assert stub.published_at is None
    assert stub.summary is None
    assert stub.image_url is None


def test_document_without_text_keeps_metadata() -> None:
    """### A record with no extracted text stores metadata and no hash."""
    document = CanonicalDocument.from_raw(build_raw(paragraphs=[]))
    assert document.text is None
    assert document.content_hash is None
    assert document.title == "عنوان تجريبي"
    assert document.source_url == "https://listing.example.test/ar/news/example"


def test_listing_entry_becomes_a_metadata_only_document() -> None:
    """### Discovery metadata is stored without inventing text."""
    stub = ArticleStub(
        url="https://listing.example.test/ar/news/example",
        title="عنوان من القائمة",
        published_at=date(2026, 1, 8),
        summary="ملخص",
        image_url="https://listing.example.test/storage/post/large/pic.png",
    )
    document = CanonicalDocument.from_listing_entry(stub, source="uae_legislation_news", language="ar")
    assert document.text is None
    assert document.content_hash is None
    assert document.source_url == stub.url
    assert document.title == "عنوان من القائمة"
    assert document.published_at == date(2026, 1, 8)
    assert document.summary == "ملخص"
    assert document.image_url == stub.image_url


def test_every_collected_text_starts_with_the_marker() -> None:
    """### Text is either absent or starts with the marker exactly once."""
    with_text = CanonicalDocument.from_raw(build_raw())
    without_text = CanonicalDocument.from_raw(build_raw(paragraphs=[]))
    assert with_text.text is not None and with_text.text.startswith(TEXT_BEGIN_MARKER)
    assert with_text.text.count(TEXT_BEGIN_MARKER) == 1
    assert without_text.text is None


def test_canonical_identity_uses_native_id_and_scopes_resources() -> None:
    """### Native document and attachment IDs produce stable parent-scoped keys."""
    document = CanonicalDocument(
        source="commerce_reports",
        native_id="report-7",
        source_url="https://commerce.example.test/news/report-7",
        title="Report",
        language="en",
        resources=(DocumentResource(title="Report PDF", native_id="file-2", url="https://files.example.test/a.pdf"),),
    )
    assert document.identity_key == "commerce_reports:id:report-7"
    assert document.resources[0].identity_key == "commerce_reports:id:report-7:resource:id:file-2"
    assert document.resources[0].parent_identity == document.identity_key


def test_canonical_url_fallback_ignores_fragment() -> None:
    """### URL identity is deterministic when the source has no native ID."""
    document = CanonicalDocument(
        source="cba_news_releases",
        source_url="https://example.test/news?id=4#section",
        title="News",
        language="en",
    )
    assert document.identity_key == "cba_news_releases:url:https://example.test/news?id=4"


def test_canonical_rejects_mismatched_text_hash() -> None:
    """### Canonical producers cannot bypass the common hash invariant."""
    with pytest.raises(ValidationError, match="content hash"):
        CanonicalDocument(
            source="test",
            source_url="https://example.test/a",
            title="A",
            language="en",
            text="TEXT-BEGIN\nBody",
            content_hash=compute_content_hash("TEXT-BEGIN\nOther"),
        )
