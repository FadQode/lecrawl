"""### Listing tests for the releases actor."""

from __future__ import annotations

from datetime import date

import pytest

from lexcrawl.actors.cba import (
    CbaApiError,
    CbaListingPage,
    article_scope,
    article_url,
    canonical_article_url,
    is_article_url,
    is_news_release,
    parse_listing,
    parse_published_at,
    record_from_payload,
)

from ..samples import (
    RELEASES_API_URL,
    RELEASES_ARTICLE_PATH,
    RELEASES_OTHER_TYPE_ID,
    RELEASES_SITE_URL,
    RELEASES_TYPE_ID,
    cba_listing_page,
    cba_listing_record,
)

SCOPE = {"host": article_scope(RELEASES_SITE_URL), "path_prefix": RELEASES_ARTICLE_PATH}
ARTICLE_URL = article_url("sample-release", site_url=RELEASES_SITE_URL, article_path=RELEASES_ARTICLE_PATH)


def parse(payload: bytes) -> CbaListingPage:
    """### Parse a listing response with the sample source configuration."""
    return parse_listing(
        payload, api_url=RELEASES_API_URL, site_url=RELEASES_SITE_URL, article_path=RELEASES_ARTICLE_PATH
    )


def test_listing_yields_release_records() -> None:
    """### A listing response becomes a record with title, date, and URL."""
    page = parse(cba_listing_page([cba_listing_record()]))

    assert len(page.records) == 1
    record = page.records[0]
    assert record.title == "Sample release title for the listings"
    assert record.url == ARTICLE_URL
    assert record.published_at == date(2026, 9, 25)
    assert record.image_url == f"{RELEASES_SITE_URL}/Assets/images/articles/tile.jpg"


def test_listing_keeps_the_article_date_over_the_posted_date() -> None:
    """### ArticleDate is preferred, with PostedDate as the documented fallback."""
    preferred = parse(cba_listing_page([cba_listing_record()])).records[0]
    assert preferred.published_at == date(2026, 9, 25)

    fallback = parse(cba_listing_page([cba_listing_record(article_date=None)])).records[0]
    assert fallback.published_at == date(2026, 9, 20)


def test_listing_skips_records_without_title_or_slug() -> None:
    """### Records that cannot form an article URL are skipped and counted."""
    payload = cba_listing_page(
        [
            cba_listing_record(),
            {"SEOName": "", "Title": "No slug", "ArticleTypes": [RELEASES_TYPE_ID]},
            {"SEOName": "no-title", "Title": "   ", "ArticleTypes": [RELEASES_TYPE_ID]},
            "not-a-record",
        ]
    )
    page = parse(payload)
    assert len(page.records) == 1
    assert page.skipped_records == 3


def test_listing_tolerates_missing_optional_metadata() -> None:
    """### A record without a date, summary, or image still parses."""
    record = record_from_payload(
        cba_listing_record(article_date=None, posted_date=None, summary=None, with_image=False),
        site_url=RELEASES_SITE_URL,
        article_path=RELEASES_ARTICLE_PATH,
    )
    assert record is not None
    assert record.published_at is None
    assert record.summary is None
    assert record.image_url is None


def test_news_release_filter_uses_the_article_type() -> None:
    """### Only records carrying the configured type are kept."""
    release = record_from_payload(cba_listing_record(), site_url=RELEASES_SITE_URL, article_path=RELEASES_ARTICLE_PATH)
    other = record_from_payload(
        cba_listing_record(slug="a-remark", type_ids=(RELEASES_OTHER_TYPE_ID,)),
        site_url=RELEASES_SITE_URL,
        article_path=RELEASES_ARTICLE_PATH,
    )
    assert release is not None and other is not None
    assert is_news_release(release, RELEASES_TYPE_ID) is True
    assert is_news_release(other, RELEASES_TYPE_ID) is False
    assert is_news_release(other, "") is True


def test_rejected_payloads_raise_a_typed_error() -> None:
    """### Non-JSON and malformed envelopes are reported, never silently empty."""
    with pytest.raises(CbaApiError, match="not JSON"):
        parse(b"<html>nope</html>")
    with pytest.raises(CbaApiError, match="not a JSON object"):
        parse(b"[]")
    with pytest.raises(CbaApiError, match="no 'Data' array"):
        parse(b'{"ResponseCode": "200"}')


def test_article_url_scope_and_normalization() -> None:
    """### Only same-host article URLs are in scope, and query strings are dropped."""
    assert is_article_url(ARTICLE_URL, **SCOPE)
    assert not is_article_url(f"{RELEASES_SITE_URL}/news/releases", **SCOPE)
    assert not is_article_url("https://example.com/article/x", **SCOPE)
    assert not is_article_url(f"{RELEASES_SITE_URL}{RELEASES_ARTICLE_PATH}", **SCOPE)
    assert f"{RELEASES_SITE_URL}{RELEASES_ARTICLE_PATH}sample-release" == ARTICLE_URL
    assert canonical_article_url(f"{ARTICLE_URL}?type=news-releases") == ARTICLE_URL
    assert canonical_article_url(f"{ARTICLE_URL}/") == ARTICLE_URL


def test_duplicate_records_share_one_canonical_url() -> None:
    """### The same article is identified by one normalized URL."""
    first = record_from_payload(cba_listing_record(), site_url=RELEASES_SITE_URL, article_path=RELEASES_ARTICLE_PATH)
    second = record_from_payload(cba_listing_record(), site_url=RELEASES_SITE_URL, article_path=RELEASES_ARTICLE_PATH)
    assert first is not None and second is not None
    assert first.url == second.url


def test_published_at_accepts_the_observed_shapes() -> None:
    """### API timestamps and page dates both parse; junk is None."""
    assert parse_published_at("2026-09-25T00:00:00") == date(2026, 9, 25)
    assert parse_published_at("2026-09-25T00:00:00-04:00") == date(2026, 9, 25)
    assert parse_published_at("September 25, 2026") == date(2026, 9, 25)
    assert parse_published_at("Sep 25, 2026") == date(2026, 9, 25)
    assert parse_published_at(None) is None
    assert parse_published_at("") is None
    assert parse_published_at("not a date") is None
    assert parse_published_at(20260925) is None


def test_article_scope_is_derived_from_the_site_url() -> None:
    """### The in-scope host comes from the configured site URL, not from code."""
    assert article_scope(RELEASES_SITE_URL) == "releases.example.test"
    assert article_scope("https://sub.example.test/base") == "sub.example.test"
