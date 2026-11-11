"""### Unit tests for the UAE article detail parsing."""

from __future__ import annotations

import pytest

from lexcrawl.actors.uae_legislation import InsufficientArticleContent, UaeExtractionError, parse_detail
from samples import UAE_LISTING_URL, uae_detail_html

ARTICLE_URL = f"{UAE_LISTING_URL}/article-1"


def test_parse_detail_extracts_fields() -> None:
    """### The verified selectors map to the expected article fields."""
    article = parse_detail(uae_detail_html(), url=ARTICLE_URL, min_body_words=3)
    assert article.title.startswith("إعلان تجريبي")
    assert str(article.published_at) == "2026-01-08"
    assert article.language == "ar"
    assert article.text.startswith("أعلنت الجهة")
    assert article.image_url == "https://listing.example.test/storage/post/large/1.png"


def test_parse_detail_missing_title_raises() -> None:
    """### A page without a title is an extraction error."""
    with pytest.raises(UaeExtractionError, match="no article title"):
        parse_detail(uae_detail_html(with_title=False), url=ARTICLE_URL, min_body_words=3)


def test_parse_detail_empty_body_raises() -> None:
    """### A page with an empty body is an extraction error."""
    with pytest.raises(UaeExtractionError, match="body is empty"):
        parse_detail(uae_detail_html(with_body=False), url=ARTICLE_URL, min_body_words=3)


def test_parse_detail_thin_body_flags_incomplete() -> None:
    """### A thin body carries its metadata but is flagged incomplete."""
    with pytest.raises(InsufficientArticleContent) as excinfo:
        parse_detail(uae_detail_html(paragraphs=("قصير.",)), url=ARTICLE_URL, min_body_words=30)
    assert excinfo.value.article.title.startswith("إعلان تجريبي")
    assert excinfo.value.words == 1


def test_parse_detail_without_date_stays_null() -> None:
    """### A genuinely missing date is left null instead of failing."""
    article = parse_detail(uae_detail_html(date_text=None), url=ARTICLE_URL, min_body_words=3)
    assert article.published_at is None
