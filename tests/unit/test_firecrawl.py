"""### Firecrawl client tests: request shape, retries, credits, and caching."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from lexcrawl.crawling.firecrawl import FirecrawlAuthError, FirecrawlClient, FirecrawlError

API_URL = "https://api.firecrawl.dev/v2/scrape"
TARGET = "https://www.commerce.gov/data-and-reports/reports"
ENVELOPE = {
    "success": True,
    "data": {
        "html": "<html><body><table></table></body></html>",
        "markdown": "# Report",
        "metadata": {"statusCode": 200, "creditsUsed": 1, "sourceURL": TARGET},
    },
}


class Sleeps:
    """### Record backoff sleeps instead of waiting."""

    def __init__(self) -> None:
        """### Start with an empty record."""
        self.values: list[float] = []

    def __call__(self, seconds: float) -> None:
        """### Record one sleep duration."""
        self.values.append(seconds)


def build_client(
    *,
    sleep: Sleeps | None = None,
    max_retries: int = 2,
    request_delay_seconds: float = 0.0,
    cache_dir: str | None = None,
) -> FirecrawlClient:
    """### Build a client with a synthetic sleep and no pacing by default."""
    return FirecrawlClient(
        api_key="test-key",
        api_url=API_URL,
        max_retries=max_retries,
        request_delay_seconds=request_delay_seconds,
        cache_dir=cache_dir,
        sleep=sleep or Sleeps(),
    )


@respx.mock
def test_scrape_sends_the_documented_request() -> None:
    """### The request uses the v2 endpoint, bearer auth, and both formats."""
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json=ENVELOPE))
    page = build_client().scrape(TARGET)

    assert page.html and page.markdown == "# Report"
    assert page.target_status == 200
    assert page.credits_used == 1
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer test-key"
    assert json.loads(request.content) == {"url": TARGET, "formats": ["html", "markdown"]}


@respx.mock
def test_transient_status_is_retried() -> None:
    """### A retryable status is retried and can still succeed."""
    route = respx.post(API_URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, json=ENVELOPE)])
    sleeps = Sleeps()
    page = build_client(sleep=sleeps, request_delay_seconds=1.0).scrape(TARGET)

    assert page.target_status == 200
    assert route.call_count == 2
    assert sleeps.values


@respx.mock
def test_rate_limit_is_retried() -> None:
    """### HTTP 429 is transient and retried within the bound."""
    route = respx.post(API_URL).mock(side_effect=[httpx.Response(429), httpx.Response(200, json=ENVELOPE)])
    page = build_client().scrape(TARGET)
    assert page.target_status == 200
    assert route.call_count == 2


@respx.mock
def test_retries_are_bounded() -> None:
    """### Persistent transient failures stop after the configured attempts."""
    route = respx.post(API_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(FirecrawlError) as error:
        build_client(max_retries=1).scrape(TARGET)
    assert route.call_count == 2
    assert "test-key" not in str(error.value)


@respx.mock
@pytest.mark.parametrize("status", [401, 403, 402])
def test_auth_failures_are_not_retried(status: int) -> None:
    """### Credential and plan rejections are permanent."""
    route = respx.post(API_URL).mock(return_value=httpx.Response(status))
    with pytest.raises(FirecrawlAuthError):
        build_client().scrape(TARGET)
    assert route.call_count == 1


@respx.mock
def test_provider_failure_is_reported() -> None:
    """### An unsuccessful envelope is an error, not a page."""
    respx.post(API_URL).mock(return_value=httpx.Response(200, json={"success": False, "error": "quota exhausted"}))
    with pytest.raises(FirecrawlError, match="quota exhausted"):
        build_client().scrape(TARGET)


@respx.mock
def test_timeout_is_retried_then_reported() -> None:
    """### Timeouts are transient and surface as errors once retries are spent."""
    route = respx.post(API_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    with pytest.raises(FirecrawlError):
        build_client(max_retries=1).scrape(TARGET)
    assert route.call_count == 2


@respx.mock
def test_cache_avoids_a_second_call(tmp_path: Path) -> None:
    """### A cached detail response costs no credits on rerun."""
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json=ENVELOPE))
    client = build_client(cache_dir=str(tmp_path))
    first = client.scrape(TARGET, cache=True)
    second = client.scrape(TARGET, cache=True)

    assert route.call_count == 1
    assert first.cached is False
    assert second.cached is True and second.credits_used == 0
    assert second.html == first.html


@respx.mock
def test_listing_scrapes_are_not_cached(tmp_path: Path) -> None:
    """### Listing pages always hit the provider so freshness is never faked."""
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json=ENVELOPE))
    client = build_client(cache_dir=str(tmp_path))
    client.scrape(TARGET)
    client.scrape(TARGET)
    assert route.call_count == 2
