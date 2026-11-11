"""### Unit tests for the CBA listing feed parsing."""

from __future__ import annotations

import pytest

from lexcrawl.actors.cba import (
    CbaApiError,
    article_url,
    is_article_url,
    is_news_release,
    listing_request_body,
    parse_listing,
    parse_published_at,
)
from samples import (
    RELEASES_API_URL,
    RELEASES_ARTICLE_PATH,
    RELEASES_OTHER_TYPE_ID,
    RELEASES_SITE_URL,
    RELEASES_TYPE_ID,
    cba_listing_page,
    cba_listing_record,
)


def test_listing_request_body_matches_site_shape() -> None:
    """### The listing request carries the fields the site's own listing posts."""
    payload = listing_request_body(language="en-us", page_size=50, skip=100)
    assert '"name": "AdvContentRequest"' in payload
    assert '"MaxRecords": 50' in payload
    assert '"SkipRecords": 100' in payload


def test_article_url_and_scope() -> None:
    """### Article URLs resolve under the configured path and stay in scope."""
    url = article_url("sample-release", site_url=RELEASES_SITE_URL, article_path=RELEASES_ARTICLE_PATH)
    assert url == "https://releases.example.test/article/sample-release"
    assert is_article_url(url, host="releases.example.test", path_prefix=RELEASES_ARTICLE_PATH)
    assert not is_article_url("https://other.test/article/x", host="releases.example.test", path_prefix="/article/")


def test_parse_published_at_accepts_iso_and_page_formats() -> None:
    """### ISO and page date formats both parse, unusable values return None."""
    assert str(parse_published_at("2026-09-25T00:00:00")) == "2026-09-25"
    assert str(parse_published_at("September 25, 2026")) == "2026-09-25"
    assert parse_published_at("not a date") is None
    assert parse_published_at(None) is None


def test_parse_listing_reads_data_array() -> None:
    """### A well-formed response yields records with canonical URLs."""
    page = parse_listing(
        cba_listing_page([cba_listing_record()]),
        api_url=RELEASES_API_URL,
        site_url=RELEASES_SITE_URL,
        article_path=RELEASES_ARTICLE_PATH,
    )
    assert page.skipped_records == 0
    assert len(page.records) == 1
    record = page.records[0]
    assert record.url == "https://releases.example.test/article/sample-release"
    assert record.image_url == "https://releases.example.test/Assets/images/articles/tile.jpg"


def test_parse_listing_counts_unusable_records() -> None:
    """### Records missing a slug or title are counted, not dropped silently."""
    page = parse_listing(
        cba_listing_page([{"SEOName": "", "Title": ""}]),
        api_url=RELEASES_API_URL,
        site_url=RELEASES_SITE_URL,
        article_path=RELEASES_ARTICLE_PATH,
    )
    assert page.records == ()
    assert page.skipped_records == 1


def test_parse_listing_rejects_non_json() -> None:
    """### A non-JSON body raises a typed listing error."""
    with pytest.raises(CbaApiError):
        parse_listing(b"<html>", api_url=RELEASES_API_URL, site_url=RELEASES_SITE_URL, article_path="/article/")


def test_is_news_release_filters_by_type() -> None:
    """### Only records carrying the configured type GUID are releases."""
    record = parse_listing(
        cba_listing_page([cba_listing_record()]),
        api_url=RELEASES_API_URL,
        site_url=RELEASES_SITE_URL,
        article_path=RELEASES_ARTICLE_PATH,
    ).records[0]
    assert is_news_release(record, RELEASES_TYPE_ID)
    assert not is_news_release(record, RELEASES_OTHER_TYPE_ID)
