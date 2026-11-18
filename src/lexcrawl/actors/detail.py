"""### Base actor for "listing → detail page" sources (UAE, Commerce).

Both sources discover listing entries and then acquire each detail page through the same
provider. This base owns that shared orchestration — the anomaly guard, the detail budget,
the budget-stop handling, and the scrape → validate → cache → canonicalize ordering — so a
source only supplies its parsing and canonicalization hooks.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import ClassVar, Protocol, cast

from lexcrawl.actors.base import Actor, ActorRunResult, KnownFilter, partition_known
from lexcrawl.crawling.base import redact_url
from lexcrawl.crawling.firecrawl import FirecrawlError, FirecrawlScraper
from lexcrawl.crawling.scrape import BudgetExceededError, ScrapedPage, ScrapeError
from lexcrawl.logging import get_logger
from lexcrawl.models.document import RawArticle


class _HasArticle(Protocol):
    """### An extraction error that carries the partially parsed article."""

    article: RawArticle


class DetailAcquisitionActor[TItem, TArticle](Actor):
    """### Shared acquisition loop for sources that fetch a detail page per listing entry."""

    # $ Wording and the source-specific error classes are supplied by each concrete actor.$
    noun: ClassVar[str] = "item"
    not_configured_message: ClassVar[str] = "the acquisition provider is not configured"
    insufficient_error: ClassVar[type[Exception]] = ScrapeError
    extraction_error: ClassVar[type[Exception]] = ScrapeError

    def _init_acquisition(
        self,
        *,
        firecrawl: FirecrawlScraper | None,
        max_pages: int,
        max_detail_scrapes: int,
        min_body_words: int,
        source_name: str,
        is_known: KnownFilter | None,
        max_new_items: int | None,
    ) -> None:
        """### Store the shared provider, budgets, and identity filter."""
        self.firecrawl = firecrawl
        self.max_pages = max(1, max_pages)
        self.max_detail_scrapes = max(0, max_detail_scrapes)
        self.min_body_words = max(1, min_body_words)
        self.source_name = source_name
        self._is_known = is_known
        self._max_new_items = max_new_items
        self._logger = get_logger(f"{__name__}.{source_name}")

    def close(self) -> None:
        """### Release the Firecrawl client when it owns resources."""
        closer = getattr(self.firecrawl, "close", None)
        if callable(closer):
            closer()

    def run(self, fetcher: object) -> ActorRunResult:
        """### Discover listing entries, then acquire each new detail page, isolating failures."""
        started_at = time.monotonic()
        result = ActorRunResult(source=self.source_name)
        if self.firecrawl is None:
            result.add_error(self.not_configured_message)
            return result
        complete = False
        try:
            items, complete = self._discover(result)
            result.articles_discovered = len(items)
            self._acquire(items, result)
        except FirecrawlError as error:
            # $ A provider-wide failure stops the run and is reported, never masked as empty.$
            self._logger.exception("provider_failed", extra={"source": self.source_name})
            result.add_error(f"firecrawl failed: {error}")
        except Exception as error:
            self._logger.exception("actor_failed", extra={"source": self.source_name})
            result.add_error(f"unexpected actor failure: {error!r}")
        result.pagination_complete = complete
        result.duration_seconds = time.monotonic() - started_at
        self._logger.info(
            "actor_run_finished",
            extra={
                "source": self.source_name,
                "status": result.status,
                "documents": len(result.documents),
                "pending": result.documents_pending,
                "credits": result.provider_credits_used,
                "pagination_complete": result.pagination_complete,
            },
        )
        return result

    def _acquire(self, items: Mapping[str, TItem], result: ActorRunResult) -> None:
        """### Acquire a detail page for each new entry, within the anomaly and detail budgets."""
        pending, skipped = partition_known(
            [(self._identity(item), item) for item in items.values()], self._is_known
        )
        result.records_skipped_known += skipped
        if self._max_new_items is not None and len(pending) > self._max_new_items:
            result.anomaly_detected = True
            result.add_error(
                f"anomaly: {len(pending)} new {self.noun}(s) exceeds the {self._max_new_items} "
                "item limit; crawl skipped"
            )
            return
        for index, item in enumerate(pending):
            if index >= self.max_detail_scrapes:
                result.documents_pending = len(pending) - index
                result.add_error(
                    f"detail scrape budget reached; {result.documents_pending} {self.noun}(s) "
                    "pending for a later run"
                )
                return
            result.detail_pages_attempted += 1
            try:
                self._acquire_one(item, result)
            except BudgetExceededError:
                # $ A cap stop is not a per-item failure: stop spending and mark the rest pending.$
                result.documents_pending = len(pending) - index
                result.add_error(
                    f"monthly provider credit cap reached; remaining {self.noun}(s) are pending "
                    "for a later run"
                )
                return

    def _acquire_one(self, item: TItem, result: ActorRunResult) -> None:
        """### Acquire, parse, and canonicalize one detail page, isolating its failures."""
        url = self._detail_url(item)
        try:
            scraped = self._scrape(url)
        except BudgetExceededError:
            # $ Halt the run without recording a failed extraction that would burn an attempt.$
            raise
        except ScrapeError as error:
            self._fail_item(item, result, f"firecrawl detail failed: {error}")
            return
        result.provider_credits_used += scraped.credits_used
        if scraped.target_status is not None and scraped.target_status >= 400:
            self._fail_item(item, result, f"detail page returned HTTP {scraped.target_status}")
            return
        try:
            article = self._parse(scraped.html, url)
        except self.insufficient_error as error:
            # $ A cached copy that fails to parse is evicted so the next attempt refetches.$
            if scraped.cached:
                self._evict(url)
            partial = cast(_HasArticle, error).article
            self._store_partial(partial, item, result, str(error))
            return
        except self.extraction_error as error:
            if scraped.cached:
                self._evict(url)
            self._fail_item(item, result, f"detail extraction failed: {error}")
            return
        # $ Cache only after a successful parse.$
        if not scraped.cached:
            self._store_cache(url, scraped)
        self._store_document(article, item, result)

    def _scrape(self, url: str) -> ScrapedPage:
        """### Scrape one detail URL through the provider."""
        assert self.firecrawl is not None
        return self.firecrawl.scrape(url, cache=True)

    def _evict(self, url: str) -> None:
        """### Drop a cached page that failed to parse."""
        assert self.firecrawl is not None
        self.firecrawl.evict(url)

    def _store_cache(self, url: str, scraped: ScrapedPage) -> None:
        """### Cache a freshly obtained page after a successful parse."""
        assert self.firecrawl is not None
        self.firecrawl.store(url, scraped)

    def _note_failure(self, url: str, message: str, result: ActorRunResult) -> None:
        """### Record the shared failure counters and a redacted error message."""
        result.detail_pages_failed += 1
        result.records_missing_required_fields += 1
        result.records_without_text += 1
        result.add_error(f"{redact_url(url)}: {message}")

    def _discover(self, result: ActorRunResult) -> tuple[Mapping[str, TItem], bool]:
        """### Discover and deduplicate listing entries; return them with a completion flag."""
        raise NotImplementedError

    def _identity(self, item: TItem) -> str:
        """### Return the source-scoped identity for a listing entry."""
        raise NotImplementedError

    def _detail_url(self, item: TItem) -> str:
        """### Return the detail URL for a listing entry."""
        raise NotImplementedError

    def _parse(self, html: str, url: str) -> TArticle:
        """### Parse one detail page, raising the source's extraction errors."""
        raise NotImplementedError

    def _store_document(self, article: TArticle, item: TItem, result: ActorRunResult) -> None:
        """### Canonicalize a successfully parsed detail page and append it."""
        raise NotImplementedError

    def _store_partial(self, article: RawArticle, item: TItem, result: ActorRunResult, error: str) -> None:
        """### Record a parsed-but-incomplete page (kept metadata, no text)."""
        raise NotImplementedError

    def _fail_item(self, item: TItem, result: ActorRunResult, message: str) -> None:
        """### Record an unusable detail page while preserving its discovered metadata."""
        raise NotImplementedError
