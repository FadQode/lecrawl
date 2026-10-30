"""### Fetching contract shared by crawling strategies and source actors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """### One HTTP response normalized for downstream parsing."""

    url: str
    status_code: int | None
    content: bytes
    content_type: str | None = None


class FetchError(RuntimeError):
    """### Base failure for any fetch that produced no usable page."""

    def __init__(self, message: str, *, url: str, status_code: int | None = None) -> None:
        """### Record the failure with the URL and optional status code."""
        super().__init__(message)
        self.url = url
        self.status_code = status_code


class TransientFetchError(FetchError):
    """### Failure that is safe to retry, such as a timeout or a 5xx response."""


class NonTextContentError(FetchError):
    """### Response payload is not textual, so no source can parse it."""


class AccessBlockedError(FetchError):
    """### The source refused the request through a bot-protection wall."""

    def __init__(self, message: str, *, url: str, status_code: int, reason: str) -> None:
        """### Record the block reason reported by the site's protection layer."""
        super().__init__(message, url=url, status_code=status_code)
        self.reason = reason


class RobotsDisallowedError(FetchError):
    """### The published robots policy does not allow fetching this URL."""


@runtime_checkable
class Fetcher(Protocol):
    """### Minimal fetching interface every crawling strategy satisfies."""

    @property
    def engine_name(self) -> str:
        """### Name the strategy, so callers can report which one ran."""
        ...

    def fetch(self, url: str) -> FetchedPage:
        """### Retrieve one URL or raise a `FetchError`."""
        ...

    def fetch_binary(self, url: str) -> FetchedPage:
        """### Retrieve one URL as raw bytes, including non-textual payloads."""
        ...

    def post_json(self, url: str, *, payload: str) -> FetchedPage:
        """### Send a JSON payload and return the response body."""
        ...

    def close(self) -> None:
        """### Release whatever the strategy holds open."""
        ...


RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
BOT_BLOCK_STATUS_CODES = frozenset({403, 503})

# $ Markers emitted by Cloudflare bot protection when a request is denied.$
BOT_BLOCK_MARKERS = (
    b"Attention Required! | Cloudflare",
    b"you have been blocked",
    b"Just a moment...",
    b"cf-error-details",
)


def detect_bot_block(status_code: int | None, content: bytes) -> str | None:
    """### Identify a bot-protection denial page from its status and body."""
    if status_code not in BOT_BLOCK_STATUS_CODES:
        return None
    body = content[:65536]
    for marker in BOT_BLOCK_MARKERS:
        if marker in body:
            return marker.decode("utf-8", errors="replace")
    return None
