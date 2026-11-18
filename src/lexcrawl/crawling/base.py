"""### Fetching contract shared by crawling strategies and source actors."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


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
        safe_url = redact_url(url)
        super().__init__(message.replace(url, safe_url))
        self.url = safe_url
        self.status_code = status_code


class TransientFetchError(FetchError):
    """### Failure that is safe to retry, such as a timeout or a 5xx response."""

    def __init__(
        self,
        message: str,
        *,
        url: str,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        """### Retain an optional server-directed retry delay."""
        super().__init__(message, url=url, status_code=status_code)
        self.retry_after_seconds = retry_after_seconds


class ResponseTooLargeError(FetchError):
    """### The response exceeded the configured download limit."""


class NonTextContentError(FetchError):
    """### Response payload is not textual, so no source can parse it."""


class AccessBlockedError(FetchError):
    """### The source refused the request through a bot-protection wall."""

    def __init__(self, message: str, *, url: str, status_code: int, reason: str) -> None:
        """### Record the block reason reported by the site's protection layer."""
        super().__init__(message, url=url, status_code=status_code)
        self.reason = reason


class AccessDeniedError(FetchError):
    """### An ordinary HTTP authorization denial, distinct from bot protection."""


@runtime_checkable
class Fetcher(Protocol):
    """### Minimal fetching interface every crawling strategy satisfies."""

    @property
    def engine_name(self) -> str:
        """### Name the strategy, so callers can report which one ran."""
        ...

    def fetch(self, url: str) -> FetchedPage:
        """### Retrieve one URL as a textual page or raise a `FetchError`."""
        ...

    def fetch_binary(self, url: str, *, max_bytes: int | None = None) -> FetchedPage:
        """### Retrieve a bounded raw-byte response, such as a PDF attachment."""
        ...

    def post_json(self, url: str, *, payload: str) -> FetchedPage:
        """### Send a JSON payload and return the response body."""
        ...

    def close(self) -> None:
        """### Release whatever the strategy holds open."""
        ...


RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
BOT_BLOCK_STATUS_CODES = frozenset({403, 503})

# $ Markers emitted by common bot protection when a request is denied.$
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


def redact_url(url: str) -> str:
    """### Remove credential-like query values before a URL enters logs or errors."""
    parts = urlparse(url)
    sensitive = {"api_key", "apikey", "api-key", "access_token", "token", "key", "password", "secret"}
    query = [
        (name, "[REDACTED]" if name.lower() in sensitive else value)
        for name, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunparse(parts._replace(query=urlencode(query)))


_SECRET_PATTERNS = (
    (re.compile(r"(://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|token|secret|signature|sig|x-amz-signature|authorization)"
            r"(\s*[=:]\s*)[^\s&]+"
        ),
        r"\1\2***",
    ),
)


def redact_message(text: str, *, limit: int = 2000) -> str:
    """### Remove credential-like fragments from a message before it is logged or stored."""
    redacted = text
    for pattern, replacement in _SECRET_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted[:limit]


def validate_http_url(
    url: str, *, allowed_hosts: tuple[str, ...] = (), allowed_suffixes: tuple[str, ...] = ()
) -> str:
    """### Validate a request destination and an optional hostname allowlist."""
    parts = urlparse(url)
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme not in {"http", "https"} or not host or parts.username or parts.password:
        raise FetchError("destination must be an absolute HTTP(S) URL without credentials", url=url)
    if host == "localhost" or host.endswith((".localhost", ".local")):
        raise FetchError("local network destinations are not allowed", url=url)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise FetchError("non-public IP destinations are not allowed", url=url)
    if allowed_hosts or allowed_suffixes:
        permitted = {value.lower().rstrip(".") for value in allowed_hosts}
        host_allowed = host in permitted or any(
            host == suffix.lower().lstrip(".") or host.endswith("." + suffix.lower().lstrip("."))
            for suffix in allowed_suffixes
        )
        if not host_allowed:
            raise FetchError("destination host is not allowed", url=url)
    return url
