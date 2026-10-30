"""### Shared test doubles and clocks for the LexCrawl suite."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from lexcrawl.config import Settings
from lexcrawl.crawling.base import FetchedPage, FetchError


class StubFetcher:
    """### Serve canned payloads so actors can be tested without the network."""

    engine_name = "stub"

    def __init__(
        self,
        pages: dict[str, bytes],
        *,
        failures: dict[str, Exception] | None = None,
        content_type: str = "text/html",
        post_responses: list[bytes] | None = None,
    ) -> None:
        """### Map URLs to payload bodies, failures, and posted responses."""
        self._pages = pages
        self._failures = failures or {}
        self._content_type = content_type
        self._post_responses = list(post_responses or [])
        self.calls: list[str] = []
        self.posts: list[tuple[str, str]] = []

    def fetch(self, url: str) -> FetchedPage:
        """### Return a canned payload, raise a canned failure, or fail loudly."""
        self.calls.append(url)
        if url in self._failures:
            raise self._failures[url]
        if url not in self._pages:
            raise FetchError("no canned payload for URL", url=url)
        return FetchedPage(url=url, status_code=200, content=self._pages[url], content_type=self._content_type)

    def fetch_binary(self, url: str) -> FetchedPage:
        """### Return a canned payload as raw bytes."""
        return self.fetch(url)

    def post_json(self, url: str, *, payload: str) -> FetchedPage:
        """### Return the next canned response for a JSON POST."""
        self.posts.append((url, payload))
        if not self._post_responses:
            return self.fetch(url)
        index = min(len(self.posts) - 1, len(self._post_responses) - 1)
        return FetchedPage(
            url=url,
            status_code=200,
            content=self._post_responses[index],
            content_type="application/json",
        )

    def call_count(self, url: str) -> int:
        """### Count how many times a URL was requested."""
        return self.calls.count(url)

    def close(self) -> None:
        """### Release nothing; the stub holds no resources."""
        return None


class ClockRecorder:
    """### Record sleeps and advance a synthetic clock instead of waiting."""

    def __init__(self) -> None:
        """### Start the synthetic clock at a fixed origin."""
        self.slept: list[float] = []
        self._now = 1000.0

    def monotonic(self) -> float:
        """### Return the current synthetic time."""
        return self._now

    def sleep(self, seconds: float) -> None:
        """### Record a sleep and advance the synthetic clock."""
        self.slept.append(seconds)
        self._now += seconds


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """### Keep tests independent of any local .env and of the ambient environment."""
    empty_settings = Settings(_env_file=None)
    monkeypatch.setattr("lexcrawl.config.get_settings", lambda: empty_settings)
    monkeypatch.setattr("lexcrawl.cli.get_settings", lambda: empty_settings)


@pytest.fixture
def clock() -> ClockRecorder:
    """### Provide a fresh synthetic clock per test."""
    return ClockRecorder()


@pytest.fixture
def stub_fetcher_factory() -> Iterator[type[StubFetcher]]:
    """### Expose the offline payload server to tests."""
    yield StubFetcher
