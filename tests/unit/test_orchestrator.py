"""### Unit tests for the crawl orchestrator."""

from __future__ import annotations

from types import SimpleNamespace

from lexcrawl.actors.base import Actor, ActorRunResult
from lexcrawl.models.document import CanonicalDocument, RawArticle
from lexcrawl.orchestrator import run_crawl


def _doc() -> CanonicalDocument:
    """### Build a complete canonical document."""
    return CanonicalDocument.from_raw(RawArticle(source="s", url="https://x.test/a", title="T", language="en", paragraphs=["b"]))


class FakeActor(Actor):
    """### An actor returning canned output or raising."""

    name = "fake"

    def __init__(self, *, documents: list[CanonicalDocument] | None = None, error: Exception | None = None) -> None:
        self._documents = documents or []
        self._error = error

    def run(self, fetcher: object) -> ActorRunResult:
        if self._error is not None:
            raise self._error
        result = ActorRunResult(source=self.name)
        result.documents = list(self._documents)
        return result


def _fetcher() -> object:
    """### A fetcher stub exposing only the engine name."""
    return SimpleNamespace(engine_name="stub")


def test_run_crawl_success() -> None:
    """### A clean run reports success and its documents."""
    report = run_crawl([FakeActor(documents=[_doc()])], _fetcher())  # type: ignore[arg-type]
    assert report.status == "success" and len(report.documents) == 1
    assert report.summary()["documents"] == 1


def test_run_crawl_isolates_failure() -> None:
    """### One actor crashing does not stop the others."""
    report = run_crawl([FakeActor(documents=[_doc()]), FakeActor(error=RuntimeError("boom"))], _fetcher())  # type: ignore[arg-type]
    assert report.status == "partial_failure" and len(report.documents) == 1
    assert report.results[1].errors


def test_run_crawl_all_failed() -> None:
    """### With no documents the run fails."""
    report = run_crawl([FakeActor()], _fetcher())  # type: ignore[arg-type]
    assert report.status == "failed"


def test_run_crawl_empty() -> None:
    """### No actors means a failed run."""
    assert run_crawl([], _fetcher()).status == "failed"  # type: ignore[arg-type]
