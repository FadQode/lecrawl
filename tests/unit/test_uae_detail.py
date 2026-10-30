"""### Detail parser tests for the UAE Legislation news source."""

from __future__ import annotations

from datetime import date

import pytest

from lexcrawl.actors.uae_legislation import parse_detail
from lexcrawl.models.document import TEXT_BEGIN_MARKER, ArticleContentError, CanonicalDocument

from ..samples import ARTICLE_IMAGE, ARTICLE_PARAGRAPHS, ARTICLE_PREFIX, SAMPLE_TITLE, detail_html

ARTICLE_URL = f"{ARTICLE_PREFIX}article-1"


def test_detail_sample_extracts_required_fields() -> None:
    """### The article sample yields a non-empty title, date, and body."""
    article = parse_detail(detail_html(), ARTICLE_URL)
    assert article.title == SAMPLE_TITLE
    assert "إعلان تجريبي" in article.title
    assert article.published_at == date(2026, 1, 8)
    assert article.paragraphs == list(ARTICLE_PARAGRAPHS)
    assert article.language == "ar"
    assert article.source == "uae_legislation_news"
    assert article.image_url == ARTICLE_IMAGE


def test_detail_preserves_arabic_text_and_paragraph_order() -> None:
    """### Arabic content is preserved verbatim and paragraphs keep their order."""
    article = parse_detail(detail_html(), ARTICLE_URL)
    assert article.paragraphs[0].startswith("أعلنت الجهة المعنية")
    assert "المنظومة الجديدة" in article.text
    assert "\ufffd" not in article.text
    assert article.text == "\n\n".join(ARTICLE_PARAGRAPHS)


def test_detail_canonicalization_places_text_marker() -> None:
    """### The stored document begins with `TEXT-BEGIN` exactly once."""
    document = CanonicalDocument.from_raw(parse_detail(detail_html(), ARTICLE_URL))
    assert document.text.startswith(TEXT_BEGIN_MARKER)
    assert document.text.count(TEXT_BEGIN_MARKER) == 1
    assert document.published_at == date(2026, 1, 8)
    assert document.source_url == ARTICLE_URL
    assert document.content_hash


def test_detail_canonicalization_is_stable() -> None:
    """### Repeated canonicalization of the same page yields the same hash."""
    first = CanonicalDocument.from_raw(parse_detail(detail_html(), ARTICLE_URL))
    again = CanonicalDocument.from_raw(parse_detail(detail_html(), ARTICLE_URL))
    assert first.content_hash == again.content_hash


def test_detail_handles_missing_optional_image_and_summary() -> None:
    """### A page without an image box still parses successfully."""
    article = parse_detail(detail_html(image_url=None), ARTICLE_URL)
    assert article.image_url is None
    assert article.summary is None
    assert len(article.paragraphs) == len(ARTICLE_PARAGRAPHS)
    assert article.published_at == date(2026, 1, 8)


def test_detail_raises_when_container_is_missing() -> None:
    """### A page without the article container raises a clear error."""
    with pytest.raises(ArticleContentError, match="detail container"):
        parse_detail(detail_html(with_container=False), ARTICLE_URL)


def test_detail_raises_when_title_is_missing() -> None:
    """### A page without a title raises a clear error."""
    with pytest.raises(ArticleContentError, match="title"):
        parse_detail(detail_html(with_title_block=False), ARTICLE_URL)


def test_detail_raises_when_body_is_missing() -> None:
    """### A page whose body container is empty raises a clear error."""
    with pytest.raises(ArticleContentError, match="body"):
        parse_detail(detail_html(paragraphs=()), ARTICLE_URL)


def test_detail_tolerates_missing_publication_date() -> None:
    """### A genuinely unavailable date is None, not a failure."""
    article = parse_detail(detail_html(date_text=None), ARTICLE_URL)
    assert article.published_at is None
    assert article.paragraphs == list(ARTICLE_PARAGRAPHS)
