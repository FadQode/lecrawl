"""### Unit tests for the canonical document contract."""

from __future__ import annotations

import pytest

from lexcrawl.models.document import (
    TEXT_BEGIN_MARKER,
    ArticleStub,
    CanonicalDocument,
    DocumentResource,
    RawArticle,
    compute_content_hash,
    ensure_text_marker,
    stable_identity,
)


def test_ensure_text_marker_is_idempotent() -> None:
    """### The marker is prepended once and never duplicated."""
    once = ensure_text_marker("body")
    assert once == f"{TEXT_BEGIN_MARKER}\nbody"
    assert ensure_text_marker(once) == once


def test_from_raw_completes_text_and_hash() -> None:
    """### A raw article with text becomes a complete canonical document."""
    raw = RawArticle(source="cba", url="https://x.test/a", title="T", language="en", paragraphs=["one", "two"])
    document = CanonicalDocument.from_raw(raw)
    assert document.extraction_status == "complete"
    assert document.text is not None and document.text.startswith(TEXT_BEGIN_MARKER)
    assert document.content_hash == compute_content_hash(document.text)


def test_from_raw_without_text_is_pending() -> None:
    """### A raw article with no paragraphs stays text-less and pending."""
    raw = RawArticle(source="cba", url="https://x.test/a", title="T", language="en")
    document = CanonicalDocument.from_raw(raw)
    assert document.extraction_status == "pending"
    assert document.text is None
    assert document.content_hash is None


def test_listing_entry_is_metadata_only() -> None:
    """### A listing entry stores discovery metadata without text."""
    document = CanonicalDocument.from_listing_entry(
        ArticleStub(url="https://x.test/a", title="T"),
        source="cba",
        language="en",
        native_id="slug",
    )
    assert document.text is None
    assert document.identity_key == stable_identity("cba", "slug", "https://x.test/a")


def test_hash_mismatch_is_rejected() -> None:
    """### A content hash that does not match the text is rejected."""
    with pytest.raises(ValueError, match="content hash does not match"):
        CanonicalDocument(
            source="cba",
            source_url="https://x.test/a",
            title="T",
            language="en",
            text=f"{TEXT_BEGIN_MARKER}\nbody",
            content_hash="deadbeef",
            extraction_status="complete",
        )


def test_blank_title_is_rejected() -> None:
    """### A blank required field is rejected."""
    with pytest.raises(ValueError, match="must not be blank"):
        RawArticle(source="cba", url="https://x.test/a", title="  ", language="en")


def test_month_precision_requires_month_and_year() -> None:
    """### Month precision without both parts is rejected."""
    with pytest.raises(ValueError, match="month precision requires"):
        CanonicalDocument(
            source="commerce_reports",
            source_url="https://x.test/a",
            title="Report",
            language="en",
            published_month=3,
            published_precision="month",
        )


def test_month_only_date_is_preserved() -> None:
    """### A month-year date is stored without inventing a day."""
    document = CanonicalDocument(
        source="commerce_reports",
        source_url="https://x.test/a",
        title="Report",
        language="en",
        published_month=3,
        published_year=2025,
        published_date_raw="March 2025",
        published_precision="month",
    )
    assert document.published_at is None
    assert document.published_precision == "month"


def test_complete_resource_requires_text_and_hash() -> None:
    """### A complete resource must carry text and a matching hash."""
    text = f"{TEXT_BEGIN_MARKER}\nbody"
    with pytest.raises(ValueError, match="require text and content hash"):
        DocumentResource(title="report", url="https://x.test/a.pdf", kind="pdf", status="complete")
    resource = DocumentResource(
        title="report",
        url="https://x.test/a.pdf",
        kind="pdf",
        status="complete",
        text=text,
        content_hash=compute_content_hash(text),
    )
    assert resource.text == text


def test_resource_without_url_must_be_unavailable() -> None:
    """### A resource with no URL can only be marked url_unavailable."""
    with pytest.raises(ValueError, match="without a URL"):
        DocumentResource(title="report", kind="pdf", status="pending")
