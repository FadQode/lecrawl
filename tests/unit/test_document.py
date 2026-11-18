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


def test_marker_idempotent() -> None:
    """### The marker is prepended once."""
    once = ensure_text_marker("body")
    assert once == f"{TEXT_BEGIN_MARKER}\nbody"
    assert ensure_text_marker(once) == once


def test_from_raw_complete_hashes_text() -> None:
    """### A raw article with text becomes complete with a matching hash."""
    raw = RawArticle(source="cba", url="https://x.test/a", title="T", language="en", paragraphs=["one", "two"])
    doc = CanonicalDocument.from_raw(raw)
    assert doc.extraction_status == "complete"
    assert doc.text is not None and doc.text.startswith(TEXT_BEGIN_MARKER)
    assert doc.content_hash == compute_content_hash(doc.text)


def test_from_raw_without_text_is_pending() -> None:
    """### No paragraphs means a pending, text-less document."""
    doc = CanonicalDocument.from_raw(RawArticle(source="cba", url="https://x.test/a", title="T", language="en"))
    assert doc.text is None and doc.content_hash is None and doc.extraction_status == "pending"


def test_listing_entry_metadata_only() -> None:
    """### A listing entry stores metadata only."""
    doc = CanonicalDocument.from_listing_entry(
        ArticleStub(url="https://x.test/a", title="T"), source="cba", language="en", native_id="slug"
    )
    assert doc.text is None and doc.identity_key == stable_identity("cba", "slug", "https://x.test/a")


def test_identity_strips_query_and_trailing_slash() -> None:
    """### Identity drops query/fragment/trailing slash but keeps path case."""
    ident = stable_identity("cba", None, "https://X.test/Path/?a=1#frag")
    assert ident == "cba:url:https://x.test/Path"


def test_hash_mismatch_rejected() -> None:
    """### A hash not matching the text is rejected."""
    with pytest.raises(ValueError, match="content hash does not match"):
        CanonicalDocument(
            source="cba", source_url="https://x.test/a", title="T", language="en",
            text=f"{TEXT_BEGIN_MARKER}\nbody", content_hash="deadbeef", extraction_status="complete",
        )


def test_blank_field_rejected() -> None:
    """### Blank required fields are rejected."""
    with pytest.raises(ValueError, match="must not be blank"):
        RawArticle(source="cba", url="https://x.test/a", title=" ", language="en")


def test_month_precision_requires_parts() -> None:
    """### Month precision without month/year is rejected."""
    with pytest.raises(ValueError, match="month precision requires"):
        CanonicalDocument(
            source="commerce", source_url="https://x.test/a", title="R", language="en",
            published_month=3, published_precision="month",
        )


def test_complete_resource_requires_text() -> None:
    """### A complete resource needs text and hash."""
    text = f"{TEXT_BEGIN_MARKER}\nbody"
    with pytest.raises(ValueError, match="require text and content hash"):
        DocumentResource(title="r", url="https://x.test/a.pdf", kind="pdf", status="complete")
    resource = DocumentResource(
        title="r", url="https://x.test/a.pdf", kind="pdf", status="complete",
        text=text, content_hash=compute_content_hash(text),
    )
    assert resource.text == text


def test_resource_without_url_must_be_unavailable() -> None:
    """### A URL-less resource must be marked url_unavailable."""
    with pytest.raises(ValueError, match="without a URL"):
        DocumentResource(title="r", kind="pdf", status="pending")
