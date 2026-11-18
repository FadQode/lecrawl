"""### Unit tests for the resource fetcher wrapper."""

from __future__ import annotations

from types import SimpleNamespace

from lexcrawl.services.fetchers import HttpResourceFetcher


class FakeInner:
    """### A fetcher returning canned bytes."""

    def __init__(self) -> None:
        self.max_bytes: int | None = None
        self.closed = False

    def fetch_binary(self, url: str, *, max_bytes: int) -> SimpleNamespace:
        self.max_bytes = max_bytes
        return SimpleNamespace(content=b"data")

    def close(self) -> None:
        self.closed = True


def test_fetch_delegates_and_closes() -> None:
    """### fetch passes the byte limit through; close propagates."""
    inner = FakeInner()
    fetcher = HttpResourceFetcher(inner)  # type: ignore[arg-type]
    assert fetcher.fetch("u", max_bytes=123) == b"data"
    assert inner.max_bytes == 123
    fetcher.close()
    assert inner.closed is True
