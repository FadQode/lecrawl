"""### HTTP fetcher tests: retries, timeouts, pacing, blocks, and caching."""

from __future__ import annotations

import logging

import httpx
import pytest
import respx

from lexcrawl.crawling.base import (
    AccessBlockedError,
    AccessDeniedError,
    FetchError,
    NonTextContentError,
    ResponseTooLargeError,
    RobotsDisallowedError,
    TransientFetchError,
)
from lexcrawl.crawling.http import HttpFetcher

from ..conftest import ClockRecorder

SITE = "https://listing.example.test"
ARTICLE_URL = f"{SITE}/ar/news/example-article"
OTHER_URL = f"{SITE}/ar/news/second-article"
ROBOTS_URL = f"{SITE}/robots.txt"

HTML_HEADERS = {"content-type": "text/html; charset=utf-8"}
OK_BODY = b"<html>ok</html>"
BLOCK_BODY = (
    b"<html><head><title>Attention Required! | Cloudflare</title></head>"
    b"<body>Sorry, you have been blocked</body></html>"
)


def html_response(body: bytes = OK_BODY, status_code: int = 200) -> httpx.Response:
    """### Build a mocked HTML response."""
    return httpx.Response(status_code, content=body, headers=HTML_HEADERS)


def build_fetcher(
    clock: ClockRecorder,
    *,
    max_retries: int = 3,
    backoff_seconds: float = 0.0,
    request_delay_seconds: float = 0.0,
    respect_robots_txt: bool = False,
) -> HttpFetcher:
    """### Build a fetcher with a synthetic clock and no robots checks by default."""
    return HttpFetcher(
        user_agent="LexCrawl-test/0.1",
        max_retries=max_retries,
        backoff_seconds=backoff_seconds,
        request_delay_seconds=request_delay_seconds,
        respect_robots_txt=respect_robots_txt,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


@respx.mock
def test_fetch_returns_page_on_success(clock: ClockRecorder) -> None:
    """### A successful response is returned as a normalized page."""
    respx.get(ARTICLE_URL).mock(return_value=html_response())
    page = build_fetcher(clock).fetch(ARTICLE_URL)
    assert page.status_code == 200
    assert page.content == OK_BODY
    assert page.content_type.startswith("text/html")


@respx.mock
def test_fetch_retries_transient_status(clock: ClockRecorder) -> None:
    """### A retryable status is retried and can still succeed."""
    route = respx.get(ARTICLE_URL).mock(side_effect=[httpx.Response(503), html_response()])
    page = build_fetcher(clock, max_retries=2, backoff_seconds=0.5).fetch(ARTICLE_URL)
    assert page.status_code == 200
    assert route.call_count == 2
    assert clock.slept


@respx.mock
def test_retry_after_delay_is_respected(clock: ClockRecorder) -> None:
    """### A server-provided Retry-After delay is a floor for retry timing."""
    route = respx.get(ARTICLE_URL).mock(
        side_effect=[httpx.Response(429, headers={"retry-after": "4"}), html_response()]
    )
    page = build_fetcher(clock, max_retries=1, backoff_seconds=0.1).fetch(ARTICLE_URL)

    assert page.status_code == 200
    assert route.call_count == 2
    assert clock.slept == [4.0]


@respx.mock
def test_fetch_raises_after_retries_are_exhausted(clock: ClockRecorder) -> None:
    """### Exhausted retries surface a transient failure."""
    route = respx.get(ARTICLE_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(TransientFetchError):
        build_fetcher(clock, max_retries=1, backoff_seconds=0.0).fetch(ARTICLE_URL)
    assert route.call_count == 2


@respx.mock
def test_fetch_retries_timeouts(clock: ClockRecorder) -> None:
    """### Connection timeouts are transient and retried."""
    route = respx.get(ARTICLE_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))
    with pytest.raises(TransientFetchError):
        build_fetcher(clock, max_retries=1, backoff_seconds=0.0).fetch(ARTICLE_URL)
    assert route.call_count == 2


@respx.mock
def test_fetch_does_not_retry_client_errors(clock: ClockRecorder) -> None:
    """### A 404 is reported immediately without retrying."""
    route = respx.get(ARTICLE_URL).mock(return_value=httpx.Response(404))
    with pytest.raises(FetchError) as error:
        build_fetcher(clock, max_retries=3).fetch(ARTICLE_URL)
    assert not isinstance(error.value, TransientFetchError)
    assert route.call_count == 1


@respx.mock
def test_fetch_rejects_binary_content(clock: ClockRecorder) -> None:
    """### A binary payload is reported as unusable."""
    respx.get(ARTICLE_URL).mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})
    )
    with pytest.raises(NonTextContentError):
        build_fetcher(clock).fetch(ARTICLE_URL)


@respx.mock
def test_fetch_rejects_declared_oversized_response_before_reading(clock: ClockRecorder) -> None:
    """### Content-Length prevents an oversized body from being buffered."""
    respx.get(ARTICLE_URL).mock(
        return_value=httpx.Response(200, headers={"content-length": "100", "content-type": "application/pdf"})
    )
    fetcher = HttpFetcher(user_agent="LexCrawl-test/0.1", respect_robots_txt=False, max_response_bytes=10)

    with pytest.raises(ResponseTooLargeError, match="10 byte"):
        fetcher.fetch_binary(ARTICLE_URL)


@respx.mock
def test_fetch_rejects_oversized_stream_without_content_length(clock: ClockRecorder) -> None:
    """### Streaming bytes are counted when the server omits Content-Length."""
    respx.get(ARTICLE_URL).mock(
        return_value=httpx.Response(
            200, content=b"x" * 20, headers={"content-type": "application/pdf", "transfer-encoding": "chunked"}
        )
    )
    fetcher = HttpFetcher(user_agent="LexCrawl-test/0.1", respect_robots_txt=False, max_response_bytes=10)

    with pytest.raises(ResponseTooLargeError, match="10 byte"):
        fetcher.fetch_binary(ARTICLE_URL)


@respx.mock
def test_redirect_to_foreign_host_is_rejected_before_following(clock: ClockRecorder) -> None:
    """### Redirects stay within the initial source host or configured allowlist."""
    foreign = "https://other.example.test/document"
    redirect = respx.get(ARTICLE_URL).mock(return_value=httpx.Response(302, headers={"location": foreign}))
    destination = respx.get(foreign).mock(return_value=html_response())

    with pytest.raises(FetchError, match="host is not allowed"):
        build_fetcher(clock).fetch(ARTICLE_URL)

    assert redirect.call_count == 1
    assert destination.call_count == 0


@respx.mock
def test_redirect_to_configured_source_alias_is_allowed(clock: ClockRecorder) -> None:
    """### Explicit source aliases can receive redirects without disabling validation."""
    destination = "https://www.listing.example.test/ar/news/example-article"
    respx.get(ARTICLE_URL).mock(return_value=httpx.Response(301, headers={"location": destination}))
    respx.get(destination).mock(return_value=html_response())
    fetcher = HttpFetcher(
        user_agent="LexCrawl-test/0.1", respect_robots_txt=False, allowed_redirect_hosts=("www.listing.example.test",)
    )

    assert fetcher.fetch(ARTICLE_URL).status_code == 200


@respx.mock
def test_fetch_accepts_json_api_payloads(clock: ClockRecorder) -> None:
    """### A JSON API payload is textual and therefore usable."""
    respx.get(ARTICLE_URL).mock(
        return_value=httpx.Response(
            200,
            content=b'{"data": []}',
            headers={"content-type": "application/vnd.api+json"},
        )
    )
    page = build_fetcher(clock).fetch(ARTICLE_URL)
    assert page.content == b'{"data": []}'
    assert page.content_type == "application/vnd.api+json"


@respx.mock
def test_fetch_identifies_bot_protection_block(clock: ClockRecorder) -> None:
    """### A Cloudflare denial is reported as a block, not as a parse problem."""
    respx.get(ARTICLE_URL).mock(return_value=html_response(BLOCK_BODY, status_code=403))
    with pytest.raises(AccessBlockedError) as error:
        build_fetcher(clock, max_retries=2).fetch(ARTICLE_URL)
    assert error.value.reason == "Attention Required! | Cloudflare"
    assert error.value.status_code == 403


@respx.mock
def test_fetch_classifies_plain_forbidden_as_access_denied(clock: ClockRecorder) -> None:
    """### An unmarked 403 is an access denial, not a generic fetch failure."""
    respx.get(ARTICLE_URL).mock(return_value=html_response("Forbidden", status_code=403))
    with pytest.raises(AccessDeniedError):
        build_fetcher(clock).fetch(ARTICLE_URL)


@respx.mock
def test_fetch_does_not_treat_successful_pages_as_blocks(clock: ClockRecorder) -> None:
    """### Block markers only matter on denial statuses."""
    respx.get(ARTICLE_URL).mock(return_value=html_response(BLOCK_BODY))
    assert build_fetcher(clock).fetch(ARTICLE_URL).status_code == 200


@respx.mock
def test_fetch_caches_repeated_urls(clock: ClockRecorder) -> None:
    """### The same URL is never fetched twice within a run."""
    route = respx.get(ARTICLE_URL).mock(return_value=html_response())
    fetcher = build_fetcher(clock)
    fetcher.fetch(ARTICLE_URL)
    fetcher.fetch(ARTICLE_URL)
    assert route.call_count == 1


@respx.mock
def test_cache_logs_redact_api_credentials(clock: ClockRecorder, caplog: pytest.LogCaptureFixture) -> None:
    """### Fetcher diagnostics retain safe URL context without logging API keys."""
    url = f"{ARTICLE_URL}?api_key=secret-value"
    respx.get(url).mock(return_value=html_response())
    fetcher = build_fetcher(clock)
    with caplog.at_level(logging.DEBUG, logger="lexcrawl.crawling.http"):
        fetcher.fetch(url)
        fetcher.fetch(url)

    assert "secret-value" not in caplog.text
    logged_urls = [record.url for record in caplog.records if hasattr(record, "url")]
    assert logged_urls == [f"{ARTICLE_URL}?api_key=%5BREDACTED%5D"]


@respx.mock
def test_fetch_enforces_request_delay_between_requests(clock: ClockRecorder) -> None:
    """### Consecutive requests are separated by the configured delay."""
    respx.get(ARTICLE_URL).mock(return_value=html_response())
    respx.get(OTHER_URL).mock(return_value=html_response())
    fetcher = build_fetcher(clock, request_delay_seconds=1.5)
    fetcher.fetch(ARTICLE_URL)
    assert clock.slept == []
    fetcher.fetch(OTHER_URL)
    assert clock.slept == [1.5]


@respx.mock
def test_fetch_rejects_urls_disallowed_by_robots(clock: ClockRecorder) -> None:
    """### The published robots policy is honoured."""
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /ar/news/\n"))
    article_route = respx.get(ARTICLE_URL).mock(return_value=html_response())
    with pytest.raises(RobotsDisallowedError):
        build_fetcher(clock, respect_robots_txt=True).fetch(ARTICLE_URL)
    assert article_route.call_count == 0


@respx.mock
def test_fetch_allows_urls_when_robots_permits(clock: ClockRecorder) -> None:
    """### An allow-all robots policy permits fetching."""
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow:\n"))
    respx.get(ARTICLE_URL).mock(return_value=html_response())
    assert build_fetcher(clock, respect_robots_txt=True).fetch(ARTICLE_URL).status_code == 200


@respx.mock
def test_robots_is_requested_once_per_host(clock: ClockRecorder) -> None:
    """### The robots policy is cached per host across requests."""
    robots_route = respx.get(ROBOTS_URL).mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow:\n"))
    respx.get(ARTICLE_URL).mock(return_value=html_response())
    respx.get(OTHER_URL).mock(return_value=html_response())
    fetcher = build_fetcher(clock, respect_robots_txt=True)
    fetcher.fetch(ARTICLE_URL)
    fetcher.fetch(OTHER_URL)
    assert robots_route.call_count == 1
