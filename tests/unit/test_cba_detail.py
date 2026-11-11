"""### Unit tests for the CBA article page parsing."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from lexcrawl.actors.cba import body_paragraphs, parse_article
from lexcrawl.models.document import TEXT_BEGIN_MARKER, ArticleContentError
from samples import cba_article_html

ARTICLE_URL = "https://releases.example.test/article/sample-release"


def test_parse_article_extracts_fields() -> None:
    """### The verified selectors map to the expected contract fields."""
    article = parse_article(cba_article_html(), ARTICLE_URL)
    assert article.title == "Sample release title for the listings"
    assert str(article.published_at) == "2026-09-25"
    assert article.type_of_law == "News Releases"
    assert article.topics == ("#Mortgages",)
    assert article.summary == "A short summary of the release points."
    assert article.text.startswith("First section heading")


def test_body_excludes_separately_rendered_sections() -> None:
    """### Summary Points and topic tags do not repeat inside the body text."""
    article = parse_article(cba_article_html(), ARTICLE_URL)
    assert "Summary Points" not in article.text
    assert "#Mortgages" not in article.text
    assert "Third section paragraph." in article.text


def test_missing_title_raises() -> None:
    """### A page without the required title fails as a content error."""
    with pytest.raises(ArticleContentError, match="title not found"):
        parse_article(cba_article_html(with_title=False), ARTICLE_URL)


def test_empty_body_raises() -> None:
    """### A page with no body text fails as a content error."""
    with pytest.raises(ArticleContentError, match="body is empty"):
        parse_article(cba_article_html(with_body=False), ARTICLE_URL)


def test_body_paragraphs_keep_reading_order() -> None:
    """### Headings and paragraphs are read in document order."""
    soup = BeautifulSoup(cba_article_html(), "lxml")
    paragraphs = body_paragraphs(soup.select_one("#section-to-print"))
    assert paragraphs == [
        "First section heading",
        "First section paragraph.",
        "Second section heading",
        "Second section paragraph.",
        "Third section heading",
        "Third section paragraph.",
    ]
    assert TEXT_BEGIN_MARKER not in " ".join(paragraphs)
