"""### Unit tests for direct HTML acquisition and its fail-closed behavior."""

from __future__ import annotations

import httpx
import pytest
import respx

from lexcrawl.crawling.direct import DirectHtmlScraper
from lexcrawl.crawling.scrape import ScrapeChallengeError, ScrapeError

URL = "https://source.test/page"


def _scraper(**overrides: object) -> DirectHtmlScraper:
    """### Build a direct scraper with a no-op delay for fast tests."""
    defaults: dict[str, object] = {
        "user_agent": "LexCrawl/0.1 (+contact)",
        "allowed_hosts": ("source.test",),
        "retry_delay_seconds": 0.0,
        "sleep": lambda _seconds: None,
    }
    defaults.update(overrides)
    return DirectHtmlScraper(**defaults)  # type: ignore[arg-type]


@respx.mock
def test_direct_fetch_returns_page() -> None:
    """### A 200 response is returned as a page with no provider credits."""
    respx.get(URL).mock(return_value=httpx.Response(200, html="<p>ok</p>", headers={"content-type": "text/html"}))
    scraper = _scraper()
    page = scraper.scrape(URL)
    scraper.close()
    assert page.html == "<p>ok</p>"
    assert page.target_status == 200
    assert page.credits_used == 0


@respx.mock
def test_challenge_retries_once_then_fails_closed() -> None:
    """### A 403 is retried once, then fails closed with a challenge error."""
    route = respx.get(URL).mock(return_value=httpx.Response(403, text="no"))
    scraper = _scraper()
    with pytest.raises(ScrapeChallengeError):
        scraper.scrape(URL)
    scraper.close()
    assert route.call_count == 2


@respx.mock
def test_transient_challenge_then_success() -> None:
    """### A single transient 403 followed by a 200 succeeds on the retry."""
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(403, text="no"),
            httpx.Response(200, text="ok", headers={"content-type": "text/html"}),
        ]
    )
    scraper = _scraper()
    page = scraper.scrape(URL)
    scraper.close()
    assert page.target_status == 200
    assert route.call_count == 2


@respx.mock
def test_non_challenge_status_is_returned_without_retry() -> None:
    """### A 404 is returned as-is for the caller to treat as a failed page."""
    route = respx.get(URL).mock(return_value=httpx.Response(404, text="missing"))
    scraper = _scraper()
    page = scraper.scrape(URL)
    scraper.close()
    assert page.target_status == 404
    assert route.call_count == 1


def test_host_outside_allowlist_is_rejected() -> None:
    """### A destination outside the allowlist fails immediately."""
    scraper = _scraper()
    with pytest.raises(ScrapeError, match="not allowed"):
        scraper.scrape("https://evil.test/page")
    scraper.close()


@respx.mock
def test_in_scope_redirect_is_followed() -> None:
    """### A redirect to an allowed host is followed."""
    respx.get(URL).mock(return_value=httpx.Response(302, headers={"location": "/final"}))
    respx.get("https://source.test/final").mock(
        return_value=httpx.Response(200, text="final", headers={"content-type": "text/html"})
    )
    scraper = _scraper()
    page = scraper.scrape(URL)
    scraper.close()
    assert page.target_status == 200
    assert page.html == "final"


@respx.mock
def test_off_scope_redirect_is_rejected() -> None:
    """### A redirect off the allowed host fails without being followed."""
    respx.get(URL).mock(return_value=httpx.Response(302, headers={"location": "https://evil.test/x"}))
    scraper = _scraper()
    with pytest.raises(ScrapeError, match="not allowed"):
        scraper.scrape(URL)
    scraper.close()
