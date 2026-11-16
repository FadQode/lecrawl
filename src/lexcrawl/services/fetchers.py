"""### Attachment byte fetchers that enforce the size limit while streaming.

A fetcher must bound peak memory: implementations should abort as soon as the payload
exceeds `max_bytes`, not after loading it fully.
"""

from __future__ import annotations

from typing import Protocol

from lexcrawl.crawling.base import Fetcher


class ResourceFetcher(Protocol):
    """### Fetch an attachment's bytes, enforcing the byte cap while streaming."""

    def fetch(self, url: str, *, max_bytes: int) -> bytes:
        """### Return the payload, raising if it exceeds `max_bytes` mid-stream."""
        ...


class HttpResourceFetcher:
    """### Fetch attachments over HTTP with a streaming byte cap."""

    def __init__(self, fetcher: Fetcher) -> None:
        """### Wrap an HTTP fetcher whose binary path streams and enforces limits."""
        self._fetcher = fetcher

    def fetch(self, url: str, *, max_bytes: int) -> bytes:
        """### Fetch the payload, bounding memory via the underlying streaming fetch."""
        # $ fetch_binary streams in chunks and raises ResponseTooLargeError past the cap.$
        return self._fetcher.fetch_binary(url, max_bytes=max_bytes).content
