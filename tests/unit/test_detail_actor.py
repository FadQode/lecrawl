"""### Unit tests for the shared detail-acquisition actor base."""

from __future__ import annotations

from lexcrawl.actors.base import ActorRunResult
from lexcrawl.actors.detail import DetailAcquisitionActor
from lexcrawl.crawling.scrape import BudgetExceededError, ScrapedPage, ScrapeError


class _ExtractError(Exception):
    """### A generic source extraction error."""


class _Insufficient(_ExtractError):
    """### An insufficient-body error carrying a partial article."""

    def __init__(self, article: object) -> None:
        super().__init__("insufficient body")
        self.article = article


class _FakeScraper:
    """### A provider stub that records cache activity."""

    def __init__(self, page: ScrapedPage | None = None, error: Exception | None = None) -> None:
        self.page = page
        self.error = error
        self.calls = 0
        self.stored: list[str] = []
        self.evicted: list[str] = []

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        self.calls += 1
        if self.error is not None:
            raise self.error
        assert self.page is not None
        return self.page

    def store(self, url: str, page: ScrapedPage) -> None:
        self.stored.append(url)

    def evict(self, url: str) -> None:
        self.evicted.append(url)

    def close(self) -> None:
        return None


class _Actor(DetailAcquisitionActor[str, object]):
    """### A concrete actor that records hook invocations."""

    name = "t"
    noun = "item"
    not_configured_message = "not configured"
    insufficient_error = _Insufficient
    extraction_error = _ExtractError

    def __init__(
        self,
        scraper: _FakeScraper | None,
        *,
        items: tuple[str, ...] = ("a", "b", "c"),
        is_known: object = None,
        max_new_items: int | None = None,
        max_detail: int = 20,
        parse_error: Exception | None = None,
    ) -> None:
        self._init_acquisition(
            firecrawl=scraper,
            max_pages=1,
            max_detail_scrapes=max_detail,
            min_body_words=1,
            source_name="t",
            is_known=is_known,  # type: ignore[arg-type]
            max_new_items=max_new_items,
        )
        self._items = items
        self.parse_error = parse_error
        self.partials: list[str] = []
        self.failures: list[str] = []

    def _discover(self, result: ActorRunResult) -> tuple[dict[str, str], bool]:
        return {item: item for item in self._items}, True

    def _identity(self, item: str) -> str:
        return item

    def _detail_url(self, item: str) -> str:
        return f"https://t.test/{item}"

    def _parse(self, html: str, url: str) -> object:
        if self.parse_error is not None:
            raise self.parse_error
        return object()

    def _store_document(self, article: object, item: str, result: ActorRunResult) -> None:
        result.detail_pages_succeeded += 1
        result.documents.append(object())  # type: ignore[arg-type]

    def _store_partial(self, article: object, item: str, result: ActorRunResult, error: str) -> None:
        self._note_failure(self._detail_url(item), error, result)
        self.partials.append(item)

    def _fail_item(self, item: str, result: ActorRunResult, message: str) -> None:
        self._note_failure(self._detail_url(item), message, result)
        self.failures.append(item)


def _page(*, target: int = 200, cached: bool = False, credits: int = 1) -> ScrapedPage:
    """### Build a provider page."""
    return ScrapedPage(
        requested_url="u", provider_status=200, target_status=target, html="<p>x</p>", markdown="",
        credits_used=credits, cached=cached,
    )


def test_missing_provider_reports_error() -> None:
    """### With no provider configured the run fails closed without discovering."""
    result = _Actor(None).run(object())
    assert result.status == "failed" and result.documents == []


def test_success_caches_and_records() -> None:
    """### A successful detail page is cached and canonicalized."""
    scraper = _FakeScraper(_page(credits=2))
    result = _Actor(scraper, items=("a",)).run(object())
    assert result.detail_pages_succeeded == 1 and len(result.documents) == 1
    assert result.provider_credits_used == 2 and scraper.stored == ["https://t.test/a"]


def test_cached_success_is_not_rewritten() -> None:
    """### A served-from-cache page is not stored again."""
    scraper = _FakeScraper(_page(cached=True))
    _Actor(scraper, items=("a",)).run(object())
    assert scraper.stored == []


def test_scrape_error_is_a_failure() -> None:
    """### A provider scrape error records the item as failed."""
    scraper = _FakeScraper(error=ScrapeError("boom", url="https://t.test/a"))
    actor = _Actor(scraper, items=("a",))
    actor.run(object())
    assert actor.failures == ["a"]


def test_target_error_status_is_a_failure() -> None:
    """### A target 4xx/5xx status records the item as failed."""
    actor = _Actor(_FakeScraper(_page(target=503)), items=("a",))
    actor.run(object())
    assert actor.failures == ["a"]


def test_insufficient_evicts_and_keeps_partial() -> None:
    """### A cached copy that fails to parse is evicted and kept as partial."""
    scraper = _FakeScraper(_page(cached=True))
    actor = _Actor(scraper, items=("a",), parse_error=_Insufficient(object()))
    actor.run(object())
    assert actor.partials == ["a"] and scraper.evicted == ["https://t.test/a"]


def test_extraction_error_evicts_and_fails() -> None:
    """### An extraction error evicts a cached copy and fails the item."""
    scraper = _FakeScraper(_page(cached=True))
    actor = _Actor(scraper, items=("a",), parse_error=_ExtractError("bad"))
    actor.run(object())
    assert actor.failures == ["a"] and scraper.evicted == ["https://t.test/a"]


def test_budget_stop_marks_remaining_pending() -> None:
    """### A budget stop is not a failure and leaves the rest pending."""
    scraper = _FakeScraper(error=BudgetExceededError("cap", url="https://t.test/a"))
    actor = _Actor(scraper, items=("a", "b", "c"))
    result = actor.run(object())
    assert result.documents_pending == 3 and actor.failures == []


def test_anomaly_guard_halts_before_scraping() -> None:
    """### Too many new items trips the anomaly guard with no scrape."""
    scraper = _FakeScraper(_page())
    result = _Actor(scraper, items=("a", "b", "c"), max_new_items=1).run(object())
    assert result.anomaly_detected is True and scraper.calls == 0


def test_detail_budget_marks_pending() -> None:
    """### The detail budget bounds scraping and marks the rest pending."""
    scraper = _FakeScraper(_page())
    result = _Actor(scraper, items=("a", "b", "c"), max_detail=1).run(object())
    assert scraper.calls == 1 and result.documents_pending == 2


def test_known_items_are_skipped() -> None:
    """### Known identities are counted as skipped and never scraped."""
    scraper = _FakeScraper(_page())
    result = _Actor(scraper, items=("a",), is_known=lambda keys: frozenset(keys)).run(object())
    assert result.records_skipped_known == 1 and scraper.calls == 0
