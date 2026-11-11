"""### Run source actors independently and aggregate their outcomes."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from lexcrawl.actors.base import Actor, ActorRunResult, RunStatus
from lexcrawl.crawling.base import Fetcher
from lexcrawl.logging import get_logger
from lexcrawl.models.document import CanonicalDocument


@dataclass(slots=True)
class CrawlRunReport:
    """### Aggregated outcome of one orchestrator invocation."""

    results: list[ActorRunResult] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def documents(self) -> list[CanonicalDocument]:
        """### Return every canonical document collected during the run."""
        return [document for result in self.results for document in result.documents]

    @property
    def status(self) -> RunStatus:
        """### Aggregate per-source statuses into one run status."""
        if not self.results:
            return "failed"
        statuses = {result.status for result in self.results}
        if statuses == {"success"}:
            return "success"
        if statuses == {"failed"}:
            return "failed"
        return "partial_failure"

    def summary(self) -> dict[str, object]:
        """### Build a JSON-serializable summary of the run."""
        return {
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 3),
            "documents": len(self.documents),
            "sources": [
                {
                    "source": result.source,
                    "status": result.status,
                    "documents": len(result.documents),
                    "listing_pages_succeeded": result.listing_pages_succeeded,
                    "listing_pages_attempted": result.listing_pages_attempted,
                    "articles_discovered": result.articles_discovered,
                    "article_urls_deduplicated": result.article_urls_deduplicated,
                    "records_filtered_out": result.records_filtered_out,
                    "detail_pages_succeeded": result.detail_pages_succeeded,
                    "detail_pages_attempted": result.detail_pages_attempted,
                    "records_missing_required_fields": result.records_missing_required_fields,
                    "records_without_text": result.records_without_text,
                    "pagination_complete": result.pagination_complete,
                    "duration_seconds": round(result.duration_seconds, 3),
                    "errors": list(result.errors),
                }
                for result in self.results
            ],
        }


def run_crawl(actors: list[Actor], fetcher: Fetcher) -> CrawlRunReport:
    """### Run every actor in isolation so one source cannot break the others."""
    logger = get_logger(__name__)
    started_at = time.monotonic()
    report = CrawlRunReport()
    for actor in actors:
        logger.info("actor_run_started", extra={"source": actor.name, "engine": fetcher.engine_name})
        try:
            result = actor.run(fetcher)
        except Exception as error:  # $ Boundary: an unexpected actor failure is reported, never swallowed.$
            logger.exception("actor_run_crashed", extra={"source": actor.name})
            result = ActorRunResult(source=actor.name, errors=[f"unexpected actor failure: {error!r}"])
        report.results.append(result)
    report.duration_seconds = time.monotonic() - started_at
    return report
