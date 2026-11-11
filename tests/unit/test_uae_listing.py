"""### Unit tests for the UAE listing page parsing."""

from __future__ import annotations

from lexcrawl.actors.uae_legislation import (
    article_scope,
    is_article_url,
    parse_listing,
    parse_published_date,
)
from samples import UAE_ARTICLE_PREFIX, UAE_HOST, UAE_LISTING_URL, UAE_PAGE_TWO_URL, uae_listing_html


def test_article_scope_from_listing_url() -> None:
    """### The article host and prefix derive from the listing URL."""
    host, prefix = article_scope(UAE_LISTING_URL)
    assert host == UAE_HOST
    assert prefix == UAE_ARTICLE_PREFIX


def test_parse_listing_extracts_cards_and_next_link() -> None:
    """### Cards yield stubs in order with the source's next-page link."""
    page = parse_listing(uae_listing_html(cards=3), UAE_LISTING_URL)
    assert len(page.stubs) == 3
    assert page.next_page_url == UAE_PAGE_TWO_URL
    first = page.stubs[0]
    assert first.url == f"https://{UAE_HOST}/ar/news/article-1"
    assert first.title == "عنوان الخبر رقم 1"
    assert str(first.published_at) == "2026-01-01"
    assert first.summary is None
    assert first.image_url == "https://listing.example.test/storage/post/large/1.png"


def test_parse_listing_counts_duplicates_and_skips() -> None:
    """### Repeated cards and non-article links are counted, not stored."""
    page = parse_listing(uae_listing_html(cards=2, duplicate_first=True), UAE_LISTING_URL)
    assert len(page.stubs) == 2
    assert page.duplicate_urls == 1


def test_parse_listing_without_pagination_is_terminal() -> None:
    """### A page without a next control marks the end of the listing."""
    page = parse_listing(uae_listing_html(cards=1, next_page=None), UAE_LISTING_URL)
    assert page.next_page_url is None


def test_parse_published_date_formats() -> None:
    """### Known source date formats parse; unusable values return None."""
    assert str(parse_published_date("Jan 08 2026")) == "2026-01-08"
    assert str(parse_published_date("08 Jan 2026")) == "2026-01-08"
    assert parse_published_date("not a date") is None
    assert parse_published_date("  ") is None


def test_is_article_url_scope() -> None:
    """### Only in-scope host and path URLs are articles."""
    host, prefix = article_scope(UAE_LISTING_URL)
    assert is_article_url(f"{UAE_LISTING_URL}/article-1", host=host, path_prefix=prefix)
    assert not is_article_url("https://other.test/ar/news/x", host=host, path_prefix=prefix)
    assert not is_article_url(f"https://{UAE_HOST}/ar/other/x", host=host, path_prefix=prefix)
