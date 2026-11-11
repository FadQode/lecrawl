"""### Provider-neutral scraping contract shared by acquisition strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ScrapedPage:
    """### One acquired page, normalized across acquisition providers."""

    requested_url: str
    provider_status: int
    target_status: int | None
    html: str
    markdown: str
    credits_used: int
    cached: bool = False


class ScrapeError(RuntimeError):
    """### Raised when an acquisition cannot produce a usable page."""

    def __init__(self, message: str, *, url: str, status_code: int | None = None, retryable: bool = False) -> None:
        """### Record the failure with its target URL and HTTP status."""
        super().__init__(message)
        self.url = url
        self.status_code = status_code
        self.retryable = retryable


class ScrapeChallengeError(ScrapeError):
    """### The source refused the request with a challenge; the caller must fail closed."""


class Scraper(Protocol):
    """### Minimal acquisition interface the source actors depend on."""

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Acquire one URL or raise a `ScrapeError`."""
        ...
