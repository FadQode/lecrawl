"""### Unit tests for the Firecrawl client."""

from __future__ import annotations

import gzip
import json

import httpx
import pytest
import respx

from lexcrawl.crawling.firecrawl import FirecrawlAuthError, FirecrawlClient, FirecrawlError

API_URL = "https://api.firecrawl.test/v2/scrape"
TARGET = "https://source.test/page"


def _client(**overrides: object) -> FirecrawlClient:
    """### Build a client with pacing and backoff disabled for fast tests."""
    defaults: dict[str, object] = {
        "api_key": "test-key",
        "api_url": API_URL,
        "request_delay_seconds": 0.0,
        "sleep": lambda _seconds: None,
    }
    defaults.update(overrides)
    return FirecrawlClient(**defaults)  # type: ignore[arg-type]


def _ok_body(*, html: str = "<p>hi</p>", credits: int = 1, status: int = 200) -> dict[str, object]:
    """### Build a successful Firecrawl envelope."""
    return {
        "success": True,
        "data": {"html": html, "markdown": "", "metadata": {"statusCode": status, "creditsUsed": credits}},
    }


@respx.mock
def test_scrape_returns_page() -> None:
    """### A successful scrape exposes html, markdown, and credits."""
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=_ok_body()))
    with _client() as client:
        page = client.scrape(TARGET)
    assert page.html == "<p>hi</p>"
    assert page.target_status == 200
    assert page.credits_used == 1
    assert page.cached is False


@respx.mock
def test_scrape_auth_error_is_not_retried() -> None:
    """### A 401 surfaces as an auth error without retrying."""
    route = respx.post(API_URL).mock(return_value=httpx.Response(401, json={"success": False, "error": "no key"}))
    with _client(), pytest.raises(FirecrawlAuthError):
        _client().scrape(TARGET)
    assert route.call_count == 1


@respx.mock
def test_scrape_retries_transient_then_succeeds() -> None:
    """### A transient provider status is retried."""
    route = respx.post(API_URL).mock(
        side_effect=[
            httpx.Response(503, json={"success": False, "error": "busy"}),
            httpx.Response(200, json=_ok_body()),
        ]
    )
    with _client(max_retries=1) as client:
        page = client.scrape(TARGET)
    assert page.html == "<p>hi</p>"
    assert route.call_count == 2


@respx.mock
def test_scrape_provider_failure_raises() -> None:
    """### A provider-reported failure raises a typed error."""
    respx.post(API_URL).mock(return_value=httpx.Response(200, json={"success": False, "error": "blocked"}))
    with _client(), pytest.raises(FirecrawlError, match="blocked"):
        _client().scrape(TARGET)


@respx.mock
def test_scrape_decodes_gzip_body() -> None:
    """### A gzip-compressed body is decoded before parsing."""
    body = gzip.compress(json.dumps(_ok_body(html="<p>gz</p>")).encode())
    respx.post(API_URL).mock(return_value=httpx.Response(200, content=body))
    with _client() as client:
        page = client.scrape(TARGET)
    assert page.html == "<p>gz</p>"


@respx.mock
def test_cache_reuses_successful_scrape(tmp_path) -> None:
    """### Caching a scrape avoids a second provider call."""
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json=_ok_body()))
    with _client(cache_dir=str(tmp_path)) as client:
        first = client.scrape(TARGET, cache=True)
        second = client.scrape(TARGET, cache=True)
    assert route.call_count == 1
    assert first.html == second.html
    assert second.cached is True
