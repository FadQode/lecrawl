"""### UAE change probe: fingerprint the listing's current article set."""

from __future__ import annotations

import hashlib
import time

from lexcrawl.actors.uae_legislation import SOURCE_NAME, article_scope, parse_listing
from lexcrawl.checks.base import Check, CheckProbe
from lexcrawl.crawling.scrape import ScrapeError, Scraper
from lexcrawl.logging import get_logger

LOGGER = get_logger(__name__)


class UaeCheck(Check):
    """### Detect UAE changes from one direct listing fetch."""

    name = SOURCE_NAME

    def __init__(self, *, listing_url: str, scraper: Scraper) -> None:
        """### Store the listing URL and the (free, fail-closed) scraper."""
        self._listing_url = listing_url
        self._scraper = scraper
        self._host, self._prefix = article_scope(listing_url)

    def close(self) -> None:
        """### Release the scraper when it owns resources."""
        closer = getattr(self._scraper, "close", None)
        if callable(closer):
            closer()

    def probe(self) -> CheckProbe:
        """### Fetch the listing once and fingerprint its article set."""
        started_at = time.monotonic()
        try:
            scraped = self._scraper.scrape(self._listing_url)
        except ScrapeError as error:
            return self._failed(started_at, f"listing fetch failed: {error}")
        if scraped.target_status is not None and scraped.target_status >= 400:
            return self._failed(started_at, f"listing returned HTTP {scraped.target_status}")

        page = parse_listing(scraped.html, self._listing_url, host=self._host, path_prefix=self._prefix)
        if not page.stubs:
            return self._failed(started_at, "listing returned no article cards")

        urls = sorted(stub.url for stub in page.stubs)
        fingerprint = hashlib.sha256("\n".join(urls).encode("utf-8")).hexdigest()
        newest = max((stub.published_at for stub in page.stubs if stub.published_at), default=None)
        return CheckProbe(
            source=self.name,
            ok=True,
            fingerprint=fingerprint,
            summary=f"{len(urls)} card(s) on page 0; newest_date={newest}",
            duration_seconds=time.monotonic() - started_at,
        )

    def _failed(self, started_at: float, message: str) -> CheckProbe:
        """### Build a fail-closed probe result."""
        LOGGER.warning("uae_check_failed", extra={"source": self.name, "reason": message})
        return CheckProbe(
            source=self.name,
            ok=False,
            fingerprint=None,
            summary=message,
            duration_seconds=time.monotonic() - started_at,
            error=message,
        )
