"""### Unit tests for API content classification."""

from __future__ import annotations

import pytest

from lexcrawl.api.services.content_queries import classify_content


@pytest.mark.parametrize(
    ("source", "document_type", "expected"),
    [
        ("cba_news_releases", "News Releases", "article"),
        ("cba_news_releases", "NEWS RELEASES", "article"),
        ("uae_legislation_news", "news_article", "article"),
        ("commerce_reports", "report", "report"),
        ("commerce_reports", "Report", "report"),
        ("commerce_reports", "News Releases", "article"),
        ("uae_legislation_news", "report", "report"),
        ("commerce_reports", None, "report"),
        ("cba_news_releases", None, "article"),
        ("unknown_source", "report", "report"),
        ("unknown_source", "mystery-label", None),
    ],
)
def test_classify_content(source: str, document_type: str | None, expected: str | None) -> None:
    """### Type labels win; the source code is only a fallback."""
    assert classify_content(source, document_type) == expected
