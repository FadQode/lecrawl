"""### CBA change probe: fingerprint the sitemap's article set and newest modification."""

from __future__ import annotations

import time

from bs4 import BeautifulSoup

from lexcrawl.actors.cba import SOURCE_NAME
from lexcrawl.checks.base import Check, CheckProbe
from lexcrawl.crawling.base import Fetcher, FetchError
from lexcrawl.logging import get_logger

LOGGER = get_logger(__name__)

ARTICLE_PATH = "/article/"


class CbaCheck(Check):
    """### Detect CBA changes from one sitemap fetch."""

    name = SOURCE_NAME

    def __init__(self, *, sitemap_url: str, fetcher: Fetcher) -> None:
        """### Store the sitemap URL and the HTTP fetcher."""
        self._sitemap_url = sitemap_url
        self._fetcher = fetcher

    def close(self) -> None:
        """### Release the HTTP fetcher."""
        self._fetcher.close()

    def probe(self) -> CheckProbe:
        """### Fetch the sitemap once and fingerprint its article locs and lastmods."""
        started_at = time.monotonic()
        try:
            content = self._fetcher.fetch(self._sitemap_url).content
        except FetchError as error:
            return self._failed(started_at, f"sitemap fetch failed: {error}")

        soup = BeautifulSoup(content, "xml")
        lastmods: list[str] = []
        count = 0
        for loc in soup.find_all("loc"):
            if ARTICLE_PATH not in loc.get_text(strip=True):
                continue
            count += 1
            sibling = loc.find_next_sibling("lastmod")
            if sibling is not None and (value := sibling.get_text(strip=True)):
                lastmods.append(value)
        if count == 0:
            return self._failed(started_at, "sitemap listed no article URLs")

        newest = max(lastmods) if lastmods else "unknown"
        fingerprint = f"{count}:{newest}"
        return CheckProbe(
            source=self.name,
            ok=True,
            fingerprint=fingerprint,
            summary=f"{count} article loc(s); newest_lastmod={newest}",
            duration_seconds=time.monotonic() - started_at,
        )

    def _failed(self, started_at: float, message: str) -> CheckProbe:
        """### Build a fail-closed probe result."""
        LOGGER.warning("cba_check_failed", extra={"source": self.name, "reason": message})
        return CheckProbe(
            source=self.name,
            ok=False,
            fingerprint=None,
            summary=message,
            duration_seconds=time.monotonic() - started_at,
            error=message,
        )
