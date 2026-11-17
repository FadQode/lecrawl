"""### Commerce change probe: fingerprint the listing's newest report and total count."""

from __future__ import annotations

import time
from urllib.parse import urlparse

from lexcrawl.actors.commerce import SOURCE_NAME, build_page_url, parse_listing
from lexcrawl.checks.base import Check, CheckProbe
from lexcrawl.checks.fingerprints import commerce_fingerprint
from lexcrawl.crawling.scrape import ScrapeError, Scraper
from lexcrawl.logging import get_logger

LOGGER = get_logger(__name__)


class CommerceCheck(Check):
    """### Detect Commerce changes from one Firecrawl fetch of listing page 0."""

    name = SOURCE_NAME

    def __init__(self, *, listing_url: str, scraper: Scraper) -> None:
        """### Store the listing URL and the (Firecrawl) scraper."""
        self._listing_url = listing_url
        self._scraper = scraper
        parsed = urlparse(listing_url)
        self._host = parsed.netloc.lower()
        self._prefixes = (f"{parsed.path.rstrip('/')}/",)

    def close(self) -> None:
        """### Release the scraper when it owns resources."""
        closer = getattr(self._scraper, "close", None)
        if callable(closer):
            closer()

    def probe(self) -> CheckProbe:
        """### Fetch listing page 0 once and fingerprint its head report and count."""
        started_at = time.monotonic()
        page_url = build_page_url(self._listing_url, 0)
        try:
            scraped = self._scraper.scrape(page_url)
        except ScrapeError as error:
            return self._failed(started_at, f"listing fetch failed: {error}")
        if scraped.target_status is not None and scraped.target_status >= 400:
            return self._failed(started_at, f"listing returned HTTP {scraped.target_status}")

        page = parse_listing(
            scraped.html, page_url=page_url, page_number=0, host=self._host, path_prefixes=self._prefixes
        )
        if page is None or not page.entries:
            return self._failed(started_at, "listing returned no report rows")

        head = page.entries[0].identity
        fingerprint = commerce_fingerprint(head, page.total_reports)
        return CheckProbe(
            source=self.name,
            ok=True,
            fingerprint=fingerprint,
            summary=f"head={head.rsplit(':', 1)[-1][-48:]} total={page.total_reports}",
            duration_seconds=time.monotonic() - started_at,
            credits_used=scraped.credits_used,
        )

    def _failed(self, started_at: float, message: str) -> CheckProbe:
        """### Build a fail-closed probe result."""
        LOGGER.warning("commerce_check_failed", extra={"source": self.name, "reason": message})
        return CheckProbe(
            source=self.name,
            ok=False,
            fingerprint=None,
            summary=message,
            duration_seconds=time.monotonic() - started_at,
            error=message,
        )
