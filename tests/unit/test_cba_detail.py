"""### Article-page tests for the releases actor."""

from __future__ import annotations

from datetime import date

import pytest

from lexcrawl.actors.cba import parse_article
from lexcrawl.models.document import TEXT_BEGIN_MARKER, ArticleContentError, CanonicalDocument

from ..samples import RELEASES_SECTIONS, RELEASES_SITE_URL, cba_article_html

ARTICLE_URL = f"{RELEASES_SITE_URL}/article/sample-release"


def test_detail_extracts_the_annotated_fields() -> None:
    """### Title, date, type, topics, and summary come from the article page."""
    article = parse_article(cba_article_html(), ARTICLE_URL)

    assert article.title == "Sample release title for the listings"
    assert article.published_at == date(2026, 9, 25)
    assert article.type_of_law == "News Releases"
    assert article.topics == ("#Mortgages",)
    assert article.summary == "A short summary of the release points."
    assert article.language == "en"
    assert article.source == "cba_news_releases"
    assert article.url == ARTICLE_URL


def test_detail_extracts_the_whole_body_in_reading_order() -> None:
    """### Every heading and paragraph is captured, in order, past the fold."""
    article = parse_article(cba_article_html(), ARTICLE_URL)

    assert len(article.paragraphs) == len(RELEASES_SECTIONS) * 2
    assert article.paragraphs[0] == RELEASES_SECTIONS[0][0]
    assert article.paragraphs[1] == RELEASES_SECTIONS[0][1]
    assert article.paragraphs[-2] == RELEASES_SECTIONS[-1][0]
    assert article.paragraphs[-1] == RELEASES_SECTIONS[-1][1]


def test_detail_excludes_separately_rendered_sections_from_the_body() -> None:
    """### Summary Points, topics, and resources do not repeat inside the text."""
    article = parse_article(cba_article_html(), ARTICLE_URL)

    assert "Summary Points" not in article.text
    assert "#Mortgages" not in article.text
    assert "Sample Data" not in article.text


def test_detail_canonicalization_marks_the_text() -> None:
    """### The stored document begins with `TEXT-BEGIN` exactly once."""
    document = CanonicalDocument.from_raw(parse_article(cba_article_html(), ARTICLE_URL))
    assert document.text is not None
    assert document.text.startswith(TEXT_BEGIN_MARKER)
    assert document.text.count(TEXT_BEGIN_MARKER) == 1
    assert document.content_hash
    assert document.type_of_law == "News Releases"
    assert document.topics == ("#Mortgages",)


def test_detail_supports_multiple_topics_in_page_order() -> None:
    """### Multiple topics are preserved verbatim and in order."""
    article = parse_article(cba_article_html(topics=("#Mortgages", "#Housing", "#Banking")), ARTICLE_URL)
    assert article.topics == ("#Mortgages", "#Housing", "#Banking")


def test_detail_tolerates_missing_optional_metadata() -> None:
    """### A page without a badge, date, summary, or resources still parses."""
    article = parse_article(
        cba_article_html(type_label=None, date_text=None, summary_points=None, pdf_url=None, excel_url=None),
        ARTICLE_URL,
    )
    assert article.type_of_law is None
    assert article.published_at is None
    assert article.summary is None
    assert article.resources == ()
    assert article.paragraphs


def test_detail_reports_missing_required_content() -> None:
    """### A page without a title or without a body raises a clear error."""
    with pytest.raises(ArticleContentError, match="title"):
        parse_article(cba_article_html(with_title=False), ARTICLE_URL)
    with pytest.raises(ArticleContentError, match="body"):
        parse_article(cba_article_html(with_body=False), ARTICLE_URL)


def test_detail_raises_when_the_body_container_is_absent() -> None:
    """### A page without the body container raises a clear error."""
    html = b"<html><body><h1>Title only</h1></body></html>"
    with pytest.raises(ArticleContentError, match="not found"):
        parse_article(html, ARTICLE_URL)


def test_detail_keeps_topics_exactly_as_displayed() -> None:
    """### Topic text and capitalization are preserved, including the hash."""
    article = parse_article(cba_article_html(topics=("#Consumer-Directed Finance",)), ARTICLE_URL)
    assert article.topics == ("#Consumer-Directed Finance",)


def test_detail_collapses_whitespace_without_losing_words() -> None:
    """### Surrounding whitespace in the source markup is normalized."""
    html = cba_article_html(sections=(("Heading", "  Spaced   out   sentence.  "),))
    article = parse_article(html, ARTICLE_URL)
    assert article.paragraphs == ["Heading", "Spaced out sentence."]


def test_detail_collects_additional_information_resources() -> None:
    """### The Additional information block is captured with formats and URLs."""
    article = parse_article(cba_article_html(), ARTICLE_URL)

    assert len(article.resources) == 2
    pdf, excel = article.resources
    assert pdf.kind == "pdf"
    assert pdf.text is None
    assert pdf.url == f"{RELEASES_SITE_URL}/Assets/documents/statistics/sample-data.pdf"
    assert excel.kind == "excel"
    assert excel.error is None
