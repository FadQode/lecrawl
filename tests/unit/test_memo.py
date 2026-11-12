"""### Unit tests for run-scoped scraper memoization."""

from __future__ import annotations

from lexcrawl.crawling.memo import MemoizingScraper
from lexcrawl.crawling.scrape import ScrapedPage


class FakeScraper:
    """### Returns a page per URL and records its calls."""

    def __init__(self) -> None:
        """### Start with no calls."""
        self.calls: list[str] = []
        self.closed = False

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Record and return a page for the URL."""
        self.calls.append(url)
        return ScrapedPage(
            requested_url=url, provider_status=200, target_status=200, html=f"<p>{url}</p>", markdown="", credits_used=1
        )

    def close(self) -> None:
        """### Mark closed."""
        self.closed = True


def test_same_url_is_fetched_once() -> None:
    """### A repeated URL within a run reuses the first page."""
    inner = FakeScraper()
    memo = MemoizingScraper(inner)
    first = memo.scrape("https://x.test/a")
    second = memo.scrape("https://x.test/a")
    assert first is second
    assert inner.calls == ["https://x.test/a"]


def test_distinct_urls_are_fetched() -> None:
    """### Different URLs are fetched independently."""
    inner = FakeScraper()
    memo = MemoizingScraper(inner)
    memo.scrape("https://x.test/a")
    memo.scrape("https://x.test/b")
    assert inner.calls == ["https://x.test/a", "https://x.test/b"]


def test_close_propagates() -> None:
    """### Closing the wrapper closes the wrapped strategy."""
    inner = FakeScraper()
    MemoizingScraper(inner).close()
    assert inner.closed
