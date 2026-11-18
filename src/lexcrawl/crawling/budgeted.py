"""### Provider-level credit budget wrapper around a scraping strategy."""

from __future__ import annotations

from typing import Protocol

from lexcrawl.crawling.scrape import BudgetExceededError, ScrapedPage, Scraper


class BudgetLike(Protocol):
    """### Minimal budget interface: committed credits for a period."""

    def committed(self, period: str) -> int:
        """### Return credits already committed for the period."""
        ...


class BudgetedScraper:
    """### Refuse provider calls once the monthly credit cap is reached."""

    def __init__(self, inner: Scraper, *, budget: BudgetLike, period: str, cap: int) -> None:
        """### Wrap a paid provider with a budget ceiling."""
        self._inner = inner
        self._budget = budget
        self._period = period
        self._cap = cap

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Check the budget before every call, then delegate.

        Locks are per source, so UAE and Commerce can spend concurrently; re-reading the
        committed total on each call keeps the cap from being overshot mid-run.
        """
        if self._cap > 0 and self._budget.committed(self._period) >= self._cap:
            raise BudgetExceededError("monthly provider credit cap reached", url=url)
        return self._inner.scrape(url, cache=cache)

    def store(self, url: str, page: ScrapedPage) -> None:
        """### Delegate cache storage."""
        store = getattr(self._inner, "store", None)
        if callable(store):
            store(url, page)

    def evict(self, url: str) -> None:
        """### Delegate cache eviction."""
        evict = getattr(self._inner, "evict", None)
        if callable(evict):
            evict(url)

    def close(self) -> None:
        """### Close the wrapped strategy."""
        closer = getattr(self._inner, "close", None)
        if callable(closer):
            closer()
