"""### The source Actor contract and its run accounting."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar, Literal

from lexcrawl.crawling.base import Fetcher
from lexcrawl.models.document import CanonicalDocument

RunStatus = Literal["success", "partial_failure", "failed"]
CheckOutcome = Literal["no_change", "new_data", "check_failed"]


@dataclass(slots=True)
class ActorRunResult:
    """### Outcome and measured counters for one actor invocation.

    Detail counters describe source-specific article units (UAE localizations, CBA releases,
    Commerce reports). Resource counters count linked attachments, never parent documents.
    """

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
    resources_discovered: int = 0
    resources_processed: int = 0
    resources_extracted: int = 0
    resources_unavailable: int = 0
    resources_failed: int = 0
    resources_skipped: int = 0
    documents_pending: int = 0
    provider_credits_used: int = 0
    check_outcome: CheckOutcome | None = None
    pagination_complete: bool = False
    duration_seconds: float = 0.0

    @property
    def status(self) -> RunStatus:
        """### Derive the run status from the preflight outcome, documents, and errors."""
        if self.check_outcome == "no_change":
            return "success"
        if self.check_outcome == "check_failed":
            return "failed"
        if not self.documents:
            return "failed"
        if self.errors:
            return "partial_failure"
        return "success"

    def add_error(self, message: str) -> None:
        """### Record one actionable error message for the run report."""
        self.errors.append(message)

    def discover_resources(self, count: int) -> None:
        """### Count links discovered before any resource processing starts."""
        self.resources_discovered += max(0, count)

    def account_resource(self, status: str) -> None:
        """### Count one processed resource by its final extraction status."""
        self.resources_processed += 1
        if status == "complete":
            self.resources_extracted += 1
        elif status == "url_unavailable":
            self.resources_unavailable += 1
        elif status in {"failed", "access_blocked"}:
            self.resources_failed += 1
        else:
            self.resources_skipped += 1


class Actor(ABC):
    """### Source-specific crawl component with a shared execution contract."""

    name: ClassVar[str]
    requires_http_engine: ClassVar[bool] = False

    @abstractmethod
    def run(self, fetcher: Fetcher) -> ActorRunResult:
        """### Discover, extract, and canonicalize this source's records."""
        ...
