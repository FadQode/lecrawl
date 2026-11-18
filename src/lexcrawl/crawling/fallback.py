"""### Composite acquisition: a direct path with a compliant fallback on a challenge."""

from __future__ import annotations

from lexcrawl.crawling.scrape import ScrapeChallengeError, ScrapedPage, Scraper
from lexcrawl.logging import get_logger

LOGGER = get_logger(__name__)


class FallbackScraper:
    """### Try the primary acquisition, falling back only when it is challenged."""

    def __init__(self, *, primary: Scraper, fallback: Scraper) -> None:
        """### Store the primary and fallback acquisition strategies."""
        self._primary = primary
        self._fallback = fallback

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Acquire a page directly, falling back to the compliant path on a challenge."""
        try:
            return self._primary.scrape(url, cache=cache)
        except ScrapeChallengeError as error:
            # $ A challenge fails closed to the compliant provider; no variation is attempted.$
            LOGGER.info("direct_scrape_challenged", extra={"url": url, "status": error.status_code})
            return self._fallback.scrape(url, cache=cache)

    def store(self, url: str, page: ScrapedPage) -> None:
        """### Forward a validated page to whichever strategies support caching."""
        for strategy in (self._primary, self._fallback):
            store = getattr(strategy, "store", None)
            if callable(store):
                store(url, page)

    def evict(self, url: str) -> None:
        """### Forward eviction to whichever strategies support caching."""
        for strategy in (self._primary, self._fallback):
            evict = getattr(strategy, "evict", None)
            if callable(evict):
                evict(url)

    def close(self) -> None:
        """### Release both acquisition strategies."""
        for strategy in (self._primary, self._fallback):
            closer = getattr(strategy, "close", None)
            if callable(closer):
                closer()
