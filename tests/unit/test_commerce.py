"""### Tests for the U.S. Department of Commerce reports actor."""

from __future__ import annotations

from datetime import date

import pytest

from lexcrawl.actors.commerce import (
    SOURCE_NAME,
    CommerceApiError,
    CommerceReportsActor,
    build_page_url,
    is_report_url,
    parse_api_page,
    parse_timestamp,
    with_api_key,
)
from lexcrawl.config import split_list
from lexcrawl.crawling.base import AccessBlockedError, FetchError

from ..conftest import StubFetcher
from ..samples import (
    REPORTS_API_URL,
    REPORTS_ARTICLE_PATHS,
    REPORTS_ARTICLE_URL,
    REPORTS_HOST,
    commerce_page,
    commerce_record,
)

API_URL = REPORTS_API_URL
SCOPE = {"host": REPORTS_HOST, "path_prefixes": REPORTS_ARTICLE_PATHS}
FIRST_PAGE_URL = build_page_url(API_URL, api_key="TEST_KEY", page_size=50)


def build_actor(
    *,
    news_types: tuple[str, ...] = ("Reports",),
    max_pages: int = 4,
) -> CommerceReportsActor:
    """### Build the actor against a test API key."""
    return CommerceReportsActor(
        api_url=API_URL,
        api_key="TEST_KEY",
        article_host=REPORTS_HOST,
        article_paths=REPORTS_ARTICLE_PATHS,
        news_types=news_types,
        max_pages=max_pages,
    )


def build_fetcher(pages: dict[str, bytes], *, failures: dict[str, Exception] | None = None) -> StubFetcher:
    """### Build a stub fetcher that serves JSON:API payloads."""
    return StubFetcher(pages, failures=failures, content_type="application/vnd.api+json")


def test_actor_collects_records_from_one_api_page() -> None:
    """### Records from a single API page become canonical documents."""
    payload = commerce_page([commerce_record(record_id="7")])
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: payload}))

    assert result.listing_pages_attempted == 1
    assert result.listing_pages_succeeded == 1
    assert result.articles_discovered == 1
    assert result.detail_pages_succeeded == 1
    assert result.pagination_complete is True
    assert result.status == "success"

    document = result.documents[0]
    assert document.source == SOURCE_NAME
    assert document.source_url == REPORTS_ARTICLE_URL
    assert document.title == "Department of Commerce publishes an annual report"
    assert document.language == "en"
    assert document.published_at == date(2026, 1, 15)
    assert document.summary == "A short teaser."
    assert document.text.startswith("TEXT-BEGIN")
    assert "First report paragraph." in document.text
    assert "Second report paragraph." in document.text
    assert document.content_hash


def test_actor_filters_out_other_news_types() -> None:
    """### The news type filter keeps reports and counts what it drops."""
    records = [
        commerce_record(record_id="1", href=f"https://{REPORTS_HOST}/reports/2026/01/report-{index}")
        for index in range(2)
    ] + [
        commerce_record(
            record_id="3",
            href=f"https://{REPORTS_HOST}/news/2026/01/fact-sheet",
            news_types=("Fact sheets",),
        )
    ]
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: commerce_page(records)}))

    assert len(result.documents) == 2
    assert result.records_filtered_out == 1
    assert result.articles_discovered == 2


def test_actor_accepts_a_custom_news_type_filter() -> None:
    """### A configured filter other than Reports is honoured."""
    record = commerce_record(news_types=("Fact sheets",))
    result = build_actor(news_types=("Fact sheets",)).run(build_fetcher({FIRST_PAGE_URL: commerce_page([record])}))
    assert len(result.documents) == 1
    assert result.records_filtered_out == 0


def test_actor_follows_api_pagination_and_adds_the_key() -> None:
    """### The API's next link is followed with the API key restored."""
    second_page = f"{REPORTS_API_URL}?q=/api/news&page%5Boffset%5D=50&page%5Blimit%5D=50"
    first = commerce_page([commerce_record(record_id="1")], next_page=second_page)
    second = commerce_page(
        [commerce_record(record_id="2", href=f"https://{REPORTS_HOST}/reports/2026/01/second-report")],
        next_page=None,
    )
    fetcher = build_fetcher({FIRST_PAGE_URL: first, with_api_key(second_page, "TEST_KEY"): second})
    result = build_actor().run(fetcher)

    assert result.listing_pages_attempted == 2
    assert result.articles_discovered == 2
    assert result.pagination_complete is True
    assert fetcher.call_count(FIRST_PAGE_URL) == 1


def test_actor_stops_at_the_page_bound() -> None:
    """### The page bound is honoured and reported as incomplete pagination."""
    second_page = f"{REPORTS_API_URL}?page%5Boffset%5D=50"
    payload = commerce_page([commerce_record()], next_page=second_page)
    result = build_actor(max_pages=1).run(build_fetcher({FIRST_PAGE_URL: payload}))

    assert result.listing_pages_attempted == 1
    assert result.pagination_complete is False
    assert any("max_pages" in error for error in result.errors)


def test_actor_deduplicates_records_by_url() -> None:
    """### The same report appearing twice yields one document."""
    record = commerce_record()
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: commerce_page([record, record])}))

    assert len(result.documents) == 1
    assert result.article_urls_deduplicated == 1


def test_actor_reports_a_blocked_api() -> None:
    """### An access denial fails the run with the protection reason recorded."""
    blocked = AccessBlockedError("source refused the request", url=FIRST_PAGE_URL, status_code=403, reason="blocked")
    result = build_actor().run(build_fetcher({}, failures={FIRST_PAGE_URL: blocked}))

    assert result.status == "failed"
    assert result.documents == []
    assert any("api fetch failed" in error for error in result.errors)


def test_actor_fails_when_the_api_is_unreachable() -> None:
    """### A transport failure is reported instead of an empty success."""
    fetcher = StubFetcher({}, failures={FIRST_PAGE_URL: FetchError("connection reset", url=FIRST_PAGE_URL)})
    result = build_actor().run(fetcher)

    assert result.status == "failed"
    assert result.listing_pages_failed == 1
    assert result.documents == []
    assert any("api fetch failed" in error for error in result.errors)


def test_actor_fails_on_malformed_json() -> None:
    """### A non-JSON body is a parsing failure, never an empty dataset."""
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: b"<html>not json</html>"}))

    assert result.status == "failed"
    assert any("api parse failed" in error for error in result.errors)


def test_actor_fails_when_nothing_matches_the_filter() -> None:
    """### No matching records is a failure, not a successful empty crawl."""
    payload = commerce_page([commerce_record(news_types=("Blog",))])
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: payload}))

    assert result.status == "failed"
    assert result.documents == []
    assert any("no report records matched" in error for error in result.errors)


def test_actor_keeps_records_without_text_as_metadata() -> None:
    """### A record with neither body nor summary is stored as metadata only."""
    payload = commerce_page([commerce_record(body=None, teaser=None, subtitle=None)])
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: payload}))

    assert result.records_without_text == 1
    assert result.detail_pages_failed == 1
    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.text is None
    assert document.title == "Department of Commerce publishes an annual report"
    assert document.published_at == date(2026, 1, 15)
    assert any("no extractable text" in error for error in result.errors)


def test_actor_falls_back_to_the_summary_when_the_body_is_missing() -> None:
    """### A record without a body still yields text from its summary."""
    payload = commerce_page([commerce_record(body=None)])
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: payload}))

    assert len(result.documents) == 1
    assert "A short teaser." in result.documents[0].text


def test_actor_reports_records_without_a_usable_date() -> None:
    """### Missing dates are counted but do not fail the record."""
    payload = commerce_page([commerce_record(post_date=None, created=None)])
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: payload}))

    assert len(result.documents) == 1
    assert result.documents[0].published_at is None
    assert any("without a usable date" in error for error in result.errors)


def test_parse_api_page_skips_unusable_records() -> None:
    """### Records without a usable URL are skipped and counted."""
    payload = commerce_page(
        [
            commerce_record(),
            commerce_record(record_id="2", href="https://example.com/reports/nope"),
            commerce_record(record_id="3", href=f"https://{REPORTS_HOST}/about/not-a-report"),
        ]
    )
    page = parse_api_page(payload, page_url=FIRST_PAGE_URL, **SCOPE)
    assert len(page.records) == 1
    assert page.skipped_records == 2


def test_parse_api_page_rejects_payloads_without_data() -> None:
    """### An envelope without a data array is rejected."""
    with pytest.raises(CommerceApiError, match="no 'data' array"):
        parse_api_page(b'{"jsonapi": {"version": "1.1"}}', page_url=FIRST_PAGE_URL, **SCOPE)


def test_parse_api_page_rejects_non_object_payloads() -> None:
    """### A JSON array is not an API envelope."""
    with pytest.raises(CommerceApiError, match="not a JSON object"):
        parse_api_page(b"[]", page_url=FIRST_PAGE_URL, **SCOPE)


def test_parse_timestamp_handles_supported_shapes() -> None:
    """### Unix seconds and common date strings are parsed, junk is None."""
    assert parse_timestamp(1768435200) == date(2026, 1, 15)
    assert parse_timestamp("1768435200") == date(2026, 1, 15)
    assert parse_timestamp("2026-01-15") == date(2026, 1, 15)
    assert parse_timestamp("2026-01-15T09:30:00") == date(2026, 1, 15)
    assert parse_timestamp("January 15, 2026") == date(2026, 1, 15)
    assert parse_timestamp(None) is None
    assert parse_timestamp(True) is None
    assert parse_timestamp("") is None
    assert parse_timestamp("not a date") is None
    assert parse_timestamp(10**20) is None


def test_report_url_scope_is_enforced() -> None:
    """### Both documented Commerce paths are accepted and nothing else."""
    assert is_report_url(f"https://{REPORTS_HOST}/reports/2026/01/example", **SCOPE)
    assert is_report_url(f"https://{REPORTS_HOST}/news/2026/01/example", **SCOPE)
    assert not is_report_url("https://example.com/reports/x", **SCOPE)
    assert not is_report_url(f"https://{REPORTS_HOST}/about/history", **SCOPE)
    assert not is_report_url(f"https://{REPORTS_HOST}/news/", **SCOPE)
    assert not is_report_url("mailto:press@example.test", **SCOPE)


def test_news_type_filter_splitting() -> None:
    """### The configured filter accepts a comma-separated list."""
    assert split_list("Reports") == ("Reports",)
    assert split_list("Reports, Fact sheets ,") == ("Reports", "Fact sheets")
    assert split_list("") == ()


def test_page_url_and_key_helpers() -> None:
    """### URL building keeps one API key and preserves page parameters."""
    assert f"{API_URL}?page%5Blimit%5D=50&api_key=TEST_KEY" == FIRST_PAGE_URL
    assert with_api_key(API_URL, "K") == f"{API_URL}?api_key=K"
    assert with_api_key(f"{API_URL}?api_key=OLD&page=2", "NEW") == f"{API_URL}?page=2&api_key=NEW"


def test_actor_requires_the_http_engine() -> None:
    """### The API source cannot be served by the rendering engine."""
    assert CommerceReportsActor.requires_http_engine is True
