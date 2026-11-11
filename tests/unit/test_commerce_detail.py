"""### Unit tests for the Commerce report detail parsing."""

from __future__ import annotations

import pytest

from lexcrawl.actors.commerce import CommerceExtractionError, InsufficientReportContent, parse_detail
from samples import COMMERCE_PDF_URL, commerce_detail_html

DETAIL_URL = "https://www.commerce.test/data-and-reports/reports/2025/01/sample-commerce-report"


def test_parse_detail_extracts_fields() -> None:
    """### The verified selectors map to the expected article fields."""
    article = parse_detail(commerce_detail_html(), url=DETAIL_URL, min_body_words=5)
    assert article.title == "Sample Commerce report"
    assert article.published_at is not None
    assert str(article.published_at) == "2025-01-15"
    assert article.published_precision == "day"
    assert article.text.startswith("The Department published")
    assert len(article.resources) == 1
    assert article.resources[0].url == COMMERCE_PDF_URL


def test_parse_detail_missing_article_raises() -> None:
    """### A page without the article container is an extraction error."""
    with pytest.raises(CommerceExtractionError, match="no article container"):
        parse_detail(commerce_detail_html(with_article=False), url=DETAIL_URL, min_body_words=5)


def test_parse_detail_missing_title_raises() -> None:
    """### A page without a title is an extraction error."""
    with pytest.raises(CommerceExtractionError, match="no report title"):
        parse_detail(commerce_detail_html(with_title=False), url=DETAIL_URL, min_body_words=5)


def test_parse_detail_short_body_flags_incomplete() -> None:
    """### A heading-only page carries its parsed metadata but is flagged incomplete."""
    with pytest.raises(InsufficientReportContent) as excinfo:
        parse_detail(commerce_detail_html(paragraphs=("Too short.",)), url=DETAIL_URL, min_body_words=30)
    assert excinfo.value.article.title == "Sample Commerce report"
    assert excinfo.value.words == 2


def test_parse_detail_without_date_stays_null() -> None:
    """### A genuinely missing date is left null instead of failing."""
    article = parse_detail(commerce_detail_html(published=None), url=DETAIL_URL, min_body_words=5)
    assert article.published_at is None
    assert article.published_precision is None
