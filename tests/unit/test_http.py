"""### Unit tests for the HTTP fetcher (respx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from lexcrawl.crawling.base import (
    AccessBlockedError,
    AccessDeniedError,
    FetchError,
    NonTextContentError,
    ResponseTooLargeError,
    TransientFetchError,
)
from lexcrawl.crawling.http import HttpFetcher


def _fetcher(**overrides: object) -> HttpFetcher:
    """### Build a fetcher with pacing/backoff disabled."""
    values: dict[str, object] = {
        "user_agent": "LexCrawl-Test/0.1",
        "request_delay_seconds": 0.0,
        "sleep": lambda _s: None,
    }
    values.update(overrides)
    return HttpFetcher(**values)  # type: ignore[arg-type]


@respx.mock
def test_fetch_text() -> None:
    """### A 200 HTML response becomes a page."""
    respx.get("https://x.test/a").mock(
        return_value=httpx.Response(200, html="<p>hi</p>", headers={"content-type": "text/html"})
    )
    with _fetcher() as fetcher:
        assert fetcher.fetch("https://x.test/a").content == b"<p>hi</p>"


@respx.mock
def test_repeat_fetch_cached() -> None:
    """### Repeats within a run hit the network once."""
    route = respx.get("https://x.test/a").mock(
        return_value=httpx.Response(200, text="ok", headers={"content-type": "text/plain"})
    )
    with _fetcher() as fetcher:
        fetcher.fetch("https://x.test/a")
        fetcher.fetch("https://x.test/a")
    assert route.call_count == 1


@respx.mock
def test_retry_transient() -> None:
    """### A 503 is retried then succeeds."""
    route = respx.get("https://x.test/a").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, text="ok", headers={"content-type": "text/plain"})]
    )
    assert _fetcher(max_retries=1).fetch("https://x.test/a").status_code == 200
    assert route.call_count == 2


@respx.mock
def test_exhausted_retries() -> None:
    """### Exhausted retries raise a transient error."""
    respx.get("https://x.test/a").mock(return_value=httpx.Response(500))
    with _fetcher(max_retries=1), pytest.raises(TransientFetchError):
        _fetcher(max_retries=1).fetch("https://x.test/a")


@respx.mock
def test_access_denied() -> None:
    """### A 403 is an authorization denial."""
    respx.get("https://x.test/a").mock(return_value=httpx.Response(403))
    with pytest.raises(AccessDeniedError):
        _fetcher().fetch("https://x.test/a")


@respx.mock
def test_bot_block_detected() -> None:
    """### A 403 challenge page is reported as blocked."""
    respx.get("https://x.test/a").mock(
        return_value=httpx.Response(
            403, content=b"Attention Required! | Cloudflare", headers={"content-type": "text/html"}
        )
    )
    with pytest.raises(AccessBlockedError):
        _fetcher().fetch("https://x.test/a")


@respx.mock
def test_non_text_rejected() -> None:
    """### A textual request against a PDF fails; binary succeeds."""
    respx.get("https://x.test/a.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})
    )
    with _fetcher() as fetcher, pytest.raises(NonTextContentError):
        fetcher.fetch("https://x.test/a.pdf")
    with _fetcher() as fetcher:
        assert fetcher.fetch_binary("https://x.test/a.pdf").content == b"%PDF-1.7"


@respx.mock
def test_size_cap() -> None:
    """### A declared body over the limit is rejected."""
    respx.get("https://x.test/big").mock(
        return_value=httpx.Response(
            200, content=b"x" * 10, headers={"content-type": "text/plain", "content-length": "10"}
        )
    )
    with _fetcher(max_response_bytes=5), pytest.raises(ResponseTooLargeError):
        _fetcher(max_response_bytes=5).fetch("https://x.test/big")


@respx.mock
def test_disallowed_redirect() -> None:
    """### A redirect off the allowlist is rejected."""
    respx.get("https://source.test/a").mock(return_value=httpx.Response(302, headers={"location": "https://evil.test/x"}))
    with _fetcher(allowed_redirect_hosts=("source.test",)), pytest.raises(FetchError, match="not allowed"):
        _fetcher(allowed_redirect_hosts=("source.test",)).fetch("https://source.test/a")


def test_request_host_allowlist() -> None:
    """### A destination outside the request allowlist is rejected."""
    with _fetcher(allowed_hosts=("source.test",)), pytest.raises(FetchError, match="not allowed"):
        _fetcher(allowed_hosts=("source.test",)).fetch("https://evil.test/x")


@respx.mock
def test_binary_response_is_not_cached() -> None:
    """### Binary payloads are never held in the in-memory cache."""
    route = respx.get("https://x.test/a.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.7", headers={"content-type": "application/pdf"})
    )
    with _fetcher() as fetcher:
        fetcher.fetch_binary("https://x.test/a.pdf")
        fetcher.fetch_binary("https://x.test/a.pdf")
    assert route.call_count == 2


@respx.mock
def test_cache_evicts_least_recently_used() -> None:
    """### The cache is bounded and evicts the least-recently-used entry."""
    route_a = respx.get("https://x.test/a").mock(
        return_value=httpx.Response(200, text="a", headers={"content-type": "text/plain"})
    )
    respx.get("https://x.test/b").mock(
        return_value=httpx.Response(200, text="b", headers={"content-type": "text/plain"})
    )
    with _fetcher(max_cache_entries=1) as fetcher:
        fetcher.fetch("https://x.test/a")
        fetcher.fetch("https://x.test/b")
        fetcher.fetch("https://x.test/a")
    assert route_a.call_count == 2


def test_host_pacer_spaces_each_host_independently() -> None:
    """### Pacing is keyed by host and tracks a single clock per host."""
    clock = {"t": 0.0}
    slept: list[float] = []

    def monotonic() -> float:
        return clock["t"]

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["t"] += seconds

    from lexcrawl.crawling.http import HostPacer

    pacer = HostPacer(sleep=sleep, monotonic=monotonic)
    pacer.wait_for_slot("a.test", 2.0)
    clock["t"] += 0.5
    pacer.wait_for_slot("a.test", 2.0)
    assert slept == [1.5]
    # $ A different host is not delayed by the first host's last request.$
    pacer.wait_for_slot("b.test", 2.0)
    assert slept == [1.5]
