"""### The source Actor contract and its run accounting."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from lexcrawl.crawling.base import Fetcher
from lexcrawl.models.document import CanonicalDocument

RunStatus = Literal["success", "partial_failure", "failed"]


@dataclass(slots=True)
class ActorRunResult:
    """### Outcome and measured counters for one actor invocation."""

    source: str
    documents: list[CanonicalDocument] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    listing_pages_attempted: int = 0
    listing_pages_succeeded: int = 0
    listing_pages_failed: int = 0
    articles_discovered: int = 0
    article_urls_deduplicated: int = 0
    records_filtered_out: int = 0
    detail_pages_attempted: int = 0
    detail_pages_succeeded: int = 0
    detail_pages_failed: int = 0
    records_missing_required_fields: int = 0
    records_without_text: int = 0
    documents_pending: int = 0
    provider_credits_used: int = 0
    pagination_complete: bool = False
    duration_seconds: float = 0.0

    @property
    def status(self) -> RunStatus:
        """### Derive the run status from the collected documents and errors."""
        if not self.documents:
            return "failed"
        if self.errors:
            return "partial_failure"
        return "success"

    def add_error(self, message: str) -> None:
        """### Record one actionable error message for the run report."""
        self.errors.append(message)


class Actor(ABC):
    """### Source-specific crawl component with a shared execution contract."""

    name: ClassVar[str]

    @abstractmethod
    def run(self, fetcher: Fetcher) -> ActorRunResult:
        """### Discover, extract, and canonicalize this source's records."""
        ...
