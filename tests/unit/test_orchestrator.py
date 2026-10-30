"""### Orchestrator tests: independent source outcomes and run aggregation."""

from __future__ import annotations

from lexcrawl.actors.base import Actor, ActorRunResult
from lexcrawl.crawling.base import Fetcher
from lexcrawl.models.document import CanonicalDocument, RawArticle
from lexcrawl.orchestrator import CrawlRunReport, run_crawl


class FakeActor(Actor):
    """### Actor double that returns a canned result or raises."""

    def __init__(self, name: str, *, documents: int = 0, error: str | None = None, crash: bool = False) -> None:
        """### Configure the canned outcome for this actor."""
        self.name = name
        self._documents = documents
        self._error = error
        self._crash = crash

    def run(self, fetcher: Fetcher) -> ActorRunResult:
        """### Return the canned result, or raise a simulated crash."""
        if self._crash:
            raise RuntimeError("actor exploded")
        result = ActorRunResult(
            source=self.name, documents=[sample_document(self.name) for _ in range(self._documents)]
        )
        if self._error:
            result.add_error(self._error)
        return result


def sample_document(source: str) -> CanonicalDocument:
    """### Build one canonical document for orchestration assertions."""
    return CanonicalDocument.from_raw(
        RawArticle(
            source=source,
            url=f"https://listing.example.test/ar/news/{source}",
            title="عنوان",
            language="ar",
            paragraphs=["نص"],
        )
    )


class NullFetcher:
    """### Fetcher double that reports any real use as a mistake."""

    engine_name = "http"


class BrowserNullFetcher(NullFetcher):
    """### Fetcher double that reports the rendering engine."""

    engine_name = "browser"


class ApiOnlyActor(FakeActor):
    """### Actor double that only works with the plain HTTP engine."""

    requires_http_engine = True

    def fetch(self, url: str) -> object:
        """### Fail loudly because orchestrator tests never fetch."""
        raise AssertionError(f"unexpected fetch of {url}")


def test_crawling_one_source_does_not_stop_the_others() -> None:
    """### An actor crash is contained and reported as a failed source."""
    report = run_crawl(
        [FakeActor("crashed", crash=True), FakeActor("healthy", documents=2)],
        NullFetcher(),
    )
    assert [result.source for result in report.results] == ["crashed", "healthy"]
    assert report.results[0].status == "failed"
    assert any("unexpected actor failure" in error for error in report.results[0].errors)
    assert report.results[1].status == "success"
    assert report.status == "partial_failure"
    assert len(report.documents) == 2


def test_report_status_is_success_only_when_every_source_succeeds() -> None:
    """### Aggregate status reflects the worst source outcome."""
    assert run_crawl([FakeActor("a", documents=1), FakeActor("b", documents=1)], NullFetcher()).status == "success"
    assert (
        run_crawl([FakeActor("a", error="partial", documents=1), FakeActor("b", documents=1)], NullFetcher()).status
        == "partial_failure"
    )
    assert run_crawl([FakeActor("a"), FakeActor("b", error="failed")], NullFetcher()).status == "failed"
    assert run_crawl([], NullFetcher()).status == "failed"


def test_summary_reports_measured_counters() -> None:
    """### The run summary exposes the metrics the report requires."""
    report = run_crawl([FakeActor("uae_legislation_news", documents=20)], NullFetcher())
    summary = report.summary()
    assert summary["status"] == "success"
    assert summary["documents"] == 20
    assert isinstance(summary["duration_seconds"], float)
    sources = summary["sources"]
    assert isinstance(sources, list)
    assert sources[0]["source"] == "uae_legislation_news"
    assert sources[0]["errors"] == []
    assert "detail_pages_succeeded" in sources[0]


def test_run_report_duration_is_measured() -> None:
    """### The report records a non-negative duration."""
    report = run_crawl([FakeActor("uae_legislation_news", documents=1)], NullFetcher())
    assert report.duration_seconds >= 0
    assert CrawlRunReport().status == "failed"


def test_api_source_is_skipped_under_the_browser_engine() -> None:
    """### A rendering engine cannot serve an API source, so it is skipped."""
    report = run_crawl([ApiOnlyActor("commerce_reports", documents=1)], BrowserNullFetcher())
    assert report.results[0].status == "failed"
    assert report.results[0].documents == []
    assert any("needs the http engine" in error for error in report.results[0].errors)


def test_api_source_runs_under_the_http_engine() -> None:
    """### The same source runs normally with the HTTP engine."""
    report = run_crawl([ApiOnlyActor("commerce_reports", documents=2)], NullFetcher())
    assert report.results[0].status == "success"
    assert len(report.documents) == 2
