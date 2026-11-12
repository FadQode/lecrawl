"""### Run-scoped memoization so one run never fetches the same page twice."""

from __future__ import annotations

from lexcrawl.crawling.scrape import ScrapedPage, Scraper


class MemoizingScraper:
    """### Reuse pages fetched earlier in the same run; never persist across runs.

    The daily runner wraps a single scraper in this and shares it between the change
    check and the actor, so the listing page fetched by the check is reused by the
    actor instead of being paid for a second time.
    """

    def __init__(self, inner: Scraper) -> None:
        """### Wrap an acquisition strategy with an in-memory page cache."""
        self._inner = inner
        self._pages: dict[str, ScrapedPage] = {}

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Return the page fetched earlier this run, or fetch and remember it."""
        existing = self._pages.get(url)
        if existing is not None:
            return existing
        page = self._inner.scrape(url, cache=cache)
        self._pages[url] = page
        return page

    def close(self) -> None:
        """### Release the wrapped strategy."""
        closer = getattr(self._inner, "close", None)
        if callable(closer):
            closer()
