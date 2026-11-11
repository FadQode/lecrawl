"""### Unit tests for the challenge-triggered fallback acquisition."""

from __future__ import annotations

import pytest

from lexcrawl.crawling.fallback import FallbackScraper
from lexcrawl.crawling.scrape import ScrapeChallengeError, ScrapedPage, ScrapeError

URL = "https://source.test/page"


def _page(url: str, html: str = "<p>ok</p>") -> ScrapedPage:
    """### Build a successful scrape result."""
    return ScrapedPage(
        requested_url=url,
        provider_status=200,
        target_status=200,
        html=html,
        markdown="",
        credits_used=0,
    )


class FakeScraper:
    """### Returns a canned page or raises a canned error, recording its calls."""

    def __init__(self, *, page: ScrapedPage | None = None, error: Exception | None = None) -> None:
        """### Store the canned outcome."""
        self._page = page
        self._error = error
        self.calls: list[str] = []
        self.closed = False

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Return the canned page or raise the canned error."""
        self.calls.append(url)
        if self._error is not None:
            raise self._error
        assert self._page is not None
        return self._page

    def close(self) -> None:
        """### Mark this scraper as closed."""
        self.closed = True


def test_primary_success_skips_fallback() -> None:
    """### A successful direct fetch never touches the fallback."""
    primary = FakeScraper(page=_page(URL, "direct"))
    fallback = FakeScraper(page=_page(URL, "fallback"))
    page = FallbackScraper(primary=primary, fallback=fallback).scrape(URL)
    assert page.html == "direct"
    assert fallback.calls == []


def test_challenge_falls_back() -> None:
    """### A challenge on the direct path falls back to the compliant provider."""
    primary = FakeScraper(error=ScrapeChallengeError("challenge", url=URL, status_code=403))
    fallback = FakeScraper(page=_page(URL, "fallback"))
    page = FallbackScraper(primary=primary, fallback=fallback).scrape(URL)
    assert page.html == "fallback"
    assert fallback.calls == [URL]


def test_non_challenge_error_propagates() -> None:
    """### Only a challenge triggers the fallback; other errors propagate."""
    primary = FakeScraper(error=ScrapeError("boom", url=URL))
    fallback = FakeScraper(page=_page(URL))
    with pytest.raises(ScrapeError):
        FallbackScraper(primary=primary, fallback=fallback).scrape(URL)
    assert fallback.calls == []


def test_close_releases_both() -> None:
    """### Closing the composite closes both strategies."""
    primary = FakeScraper(page=_page(URL))
    fallback = FakeScraper(page=_page(URL))
    FallbackScraper(primary=primary, fallback=fallback).close()
    assert primary.closed and fallback.closed
