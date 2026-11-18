"""### Unit tests for the Firecrawl client (respx)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from lexcrawl.crawling.firecrawl import FirecrawlAuthError, FirecrawlClient, FirecrawlError
from lexcrawl.crawling.scrape import ScrapedPage

API_URL = "https://api.firecrawl.test/v2/scrape"
TARGET = "https://source.test/page"


def _client(**overrides: object) -> FirecrawlClient:
    """### Build a client with pacing/backoff disabled for fast tests."""
    values: dict[str, object] = {
        "api_key": "test-key",
        "api_url": API_URL,
        "request_delay_seconds": 0.0,
        "sleep": lambda _seconds: None,
    }
    values.update(overrides)
    return FirecrawlClient(**values)  # type: ignore[arg-type]


def _body(*, html: str = "<p>hi</p>", status: int = 200, credits: int = 1) -> dict[str, object]:
    """### Build a successful provider envelope."""
    return {
        "success": True,
        "data": {"html": html, "markdown": "", "metadata": {"statusCode": status, "creditsUsed": credits}},
    }


@respx.mock
def test_scrape_success() -> None:
    """### A successful scrape returns html and credits."""
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=_body()))
    with _client() as client:
        page = client.scrape(TARGET)
    assert page.html == "<p>hi</p>" and page.target_status == 200 and page.credits_used == 1


@respx.mock
def test_auth_error_not_retried() -> None:
    """### 401 is an auth error, not retried."""
    route = respx.post(API_URL).mock(return_value=httpx.Response(401, json={"success": False, "error": "no"}))
    with _client(), pytest.raises(FirecrawlAuthError):
        _client().scrape(TARGET)
    assert route.call_count == 1


@respx.mock
def test_403_is_not_auth_error() -> None:
    """### 403 is a normal permanent error, not an auth error."""
    respx.post(API_URL).mock(return_value=httpx.Response(403, json={"success": False, "error": "blocked"}))
    with pytest.raises(FirecrawlError) as excinfo:
        _client().scrape(TARGET)
    assert not isinstance(excinfo.value, FirecrawlAuthError)


@respx.mock
def test_retries_transient() -> None:
    """### A 503 is retried before success."""
    route = respx.post(API_URL).mock(
        side_effect=[httpx.Response(503, json={"success": False}), httpx.Response(200, json=_body())]
    )
    assert _client(max_retries=1).scrape(TARGET).target_status == 200
    assert route.call_count == 2


@respx.mock
def test_honours_retry_after() -> None:
    """### A 429 Retry-After value is used for the backoff sleep."""
    sleeps: list[float] = []
    respx.post(API_URL).mock(
        side_effect=[
            httpx.Response(429, json={"success": False}, headers={"retry-after": "3"}),
            httpx.Response(200, json=_body()),
        ]
    )
    _client(max_retries=1, sleep=sleeps.append).scrape(TARGET)
    assert 3.0 in sleeps


@respx.mock
def test_response_size_cap() -> None:
    """### A body over the byte limit fails."""
    respx.post(API_URL).mock(return_value=httpx.Response(200, json=_body(html="x" * 100)))
    with pytest.raises(FirecrawlError, match="byte limit"):
        _client(max_response_bytes=10).scrape(TARGET)


@respx.mock
def test_cache_store_read_evict(tmp_path: Path) -> None:
    """### store/read-through/evict control the disk cache."""
    route = respx.post(API_URL).mock(return_value=httpx.Response(200, json=_body()))
    with _client(cache_dir=str(tmp_path), cache_ttl_seconds=0) as client:
        fresh = client.scrape(TARGET)
        assert fresh.cached is False
        client.store(TARGET, fresh)
        hit = client.scrape(TARGET, cache=True)
        assert hit.cached is True and route.call_count == 1
        client.evict(TARGET)
        client.scrape(TARGET, cache=True)
    assert route.call_count == 2


def test_store_skips_error_pages(tmp_path: Path) -> None:
    """### Error pages are never cached."""
    with _client(cache_dir=str(tmp_path)) as client:
        client.store(TARGET, ScrapedPage(TARGET, 200, 404, "", "", 1))
    assert not list(tmp_path.glob("*.json"))


def test_expired_cache_is_ignored(tmp_path: Path) -> None:
    """### A cache entry past its TTL is treated as a miss."""
    import json
    import time

    with _client(cache_dir=str(tmp_path), cache_ttl_seconds=1) as client:
        client.store(TARGET, ScrapedPage(TARGET, 200, 200, "<p>x</p>", "", 1))
        path = next(tmp_path.glob("*.json"))
        stored = json.loads(path.read_text())
        stored["fetched_at"] = time.time() - 100
        path.write_text(json.dumps(stored))
        assert client._read_cache(TARGET) is None
