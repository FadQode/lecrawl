"""### Unit tests for the HTTP crawling strategy."""

from __future__ import annotations

import httpx
import pytest
import respx

from lexcrawl.crawling.base import AccessDeniedError, NonTextContentError, ResponseTooLargeError, TransientFetchError
from lexcrawl.crawling.http import HttpFetcher


def _fetcher(**overrides: object) -> HttpFetcher:
    """### Build a fetcher with pacing and backoff disabled for fast tests."""
    defaults: dict[str, object] = {
        "user_agent": "LexCrawl-Test/0.1",
        "request_delay_seconds": 0.0,
        "sleep": lambda _seconds: None,
    }
    defaults.update(overrides)
    return HttpFetcher(**defaults)  # type: ignore[arg-type]


@respx.mock
def test_fetch_returns_textual_page() -> None:
    """### A 200 HTML response becomes a fetched page."""
    respx.get("https://x.test/a").mock(
        return_value=httpx.Response(200, html="<p>hi</p>", headers={"content-type": "text/html"})
    )
    with _fetcher() as fetcher:
        page = fetcher.fetch("https://x.test/a")
    assert page.status_code == 200
    assert page.content == b"<p>hi</p>"


@respx.mock
def test_transient_status_is_retried() -> None:
    """### A retryable status is retried and the eventual success is returned."""
    route = respx.get("https://x.test/a").mock(
        side_effect=[
            httpx.Response(503, text="busy"),
            httpx.Response(200, text="ok", headers={"content-type": "text/plain"}),
        ]
    )
    with _fetcher(max_retries=1) as fetcher:
        page = fetcher.fetch("https://x.test/a")
    assert page.status_code == 200
    assert route.call_count == 2


@respx.mock
def test_exhausted_retries_raise() -> None:
    """### When retries are exhausted the transient failure surfaces."""
    respx.get("https://x.test/a").mock(return_value=httpx.Response(500, text="boom"))
    with _fetcher(max_retries=1) as fetcher, pytest.raises(TransientFetchError):
        fetcher.fetch("https://x.test/a")


@respx.mock
def test_access_denied_raises() -> None:
    """### A 403 is reported as an authorization denial, not retried."""
    respx.get("https://x.test/a").mock(return_value=httpx.Response(403, text="no"))
    with _fetcher() as fetcher, pytest.raises(AccessDeniedError):
        fetcher.fetch("https://x.test/a")


@respx.mock
def test_non_text_content_is_rejected() -> None:
    """### A textual request against a binary payload fails clearly."""
    respx.get("https://x.test/a.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})
    )
    with _fetcher() as fetcher, pytest.raises(NonTextContentError):
        fetcher.fetch("https://x.test/a.pdf")


@respx.mock
def test_declared_oversize_is_rejected() -> None:
    """### A declared body larger than the limit is rejected before downloading."""
    respx.get("https://x.test/big").mock(
        return_value=httpx.Response(
            200, content=b"x" * 10, headers={"content-type": "text/plain", "content-length": "10"}
        )
    )
    with _fetcher(max_response_bytes=5) as fetcher, pytest.raises(ResponseTooLargeError):
        fetcher.fetch("https://x.test/big")


@respx.mock
def test_repeat_fetch_is_cached() -> None:
    """### Requesting the same URL twice within a run hits the network once."""
    route = respx.get("https://x.test/a").mock(
        return_value=httpx.Response(200, text="ok", headers={"content-type": "text/plain"})
    )
    with _fetcher() as fetcher:
        fetcher.fetch("https://x.test/a")
        fetcher.fetch("https://x.test/a")
    assert route.call_count == 1


@respx.mock
def test_post_json_sends_body() -> None:
    """### A JSON POST returns the response body."""
    route = respx.post("https://x.test/feed").mock(return_value=httpx.Response(200, json={"Data": []}))
    with _fetcher() as fetcher:
        page = fetcher.post_json("https://x.test/feed", payload='{"a": 1}')
    assert page.status_code == 200
    assert route.calls.last.request.content == b'{"a": 1}'
