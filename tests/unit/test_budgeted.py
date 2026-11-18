"""### Unit tests for the provider-level budget wrapper."""

from __future__ import annotations

import pytest

from lexcrawl.crawling.budgeted import BudgetedScraper
from lexcrawl.crawling.scrape import BudgetExceededError, ScrapedPage


def _page() -> ScrapedPage:
    """### Build a successful scrape result."""
    return ScrapedPage(
        requested_url="u", provider_status=200, target_status=200, html="<p>x</p>", markdown="", credits_used=1
    )


class FakeInner:
    """### A scraper that records calls."""

    def __init__(self) -> None:
        self.calls = 0
        self.stored: list[str] = []
        self.evicted: list[str] = []
        self.closed = False

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        self.calls += 1
        return _page()

    def store(self, url: str, page: ScrapedPage) -> None:
        self.stored.append(url)

    def evict(self, url: str) -> None:
        self.evicted.append(url)

    def close(self) -> None:
        self.closed = True


class FakeBudget:
    """### A budget with a fixed committed value."""

    def __init__(self, value: int) -> None:
        self.value = value
        self.checks = 0

    def committed(self, period: str) -> int:
        self.checks += 1
        return self.value


def test_delegates_under_cap() -> None:
    """### Under the cap, calls delegate."""
    inner, budget = FakeInner(), FakeBudget(0)
    scraper = BudgetedScraper(inner, budget=budget, period="2026-11", cap=10)
    assert scraper.scrape("u").html == "<p>x</p>"
    assert inner.calls == 1


def test_blocks_at_cap_and_checks_each_call() -> None:
    """### At the cap, provider calls are refused; the budget is re-read on every call."""
    inner, budget = FakeInner(), FakeBudget(10)
    scraper = BudgetedScraper(inner, budget=budget, period="2026-11", cap=10)
    with pytest.raises(BudgetExceededError):
        scraper.scrape("u")
    with pytest.raises(BudgetExceededError):
        scraper.scrape("u")
    assert inner.calls == 0
    # $ Locks are per source, so the committed total can change mid-run: check every call.$
    assert budget.checks == 2


def test_store_evict_close_delegate() -> None:
    """### Cache and lifecycle calls pass through."""
    inner = FakeInner()
    scraper = BudgetedScraper(inner, budget=FakeBudget(0), period="2026-11", cap=10)
    scraper.store("u", _page())
    scraper.evict("u")
    scraper.close()
    assert inner.stored == ["u"] and inner.evicted == ["u"] and inner.closed is True
