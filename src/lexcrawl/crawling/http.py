"""### HTTP crawling strategy built on httpx, with pacing and bounded retries."""

from __future__ import annotations

import logging
import random
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from threading import Lock
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt

from lexcrawl.crawling.base import (
    RETRYABLE_STATUS_CODES,
    AccessBlockedError,
    AccessDeniedError,
    FetchedPage,
    FetchError,
    NonTextContentError,
    ResponseTooLargeError,
    TransientFetchError,
    detect_bot_block,
    redact_url,
    validate_http_url,
)
from lexcrawl.logging import get_logger

# $ Textual payloads: HTML pages and the JSON/XML APIs that API sources consume.$
_TEXT_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/vnd.api+json",
    "+json",
    "application/xml",
    "application/xhtml+xml",
)

# $ Body framing headers httpx has already applied, so they must not survive a rebuild.$
_DECOMPRESSED_HEADERS = frozenset({"content-encoding", "content-length"})


class _RedactHttpxRequestUrls(logging.Filter):
    """### Remove credential query values from HTTPX request log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """### Redact URL arguments on every emitted HTTPX log record."""
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_url(str(value)) if isinstance(value, httpx.URL) else value for value in record.args
            )
        return True


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(item, _RedactHttpxRequestUrls) for item in _httpx_logger.filters):
    _httpx_logger.addFilter(_RedactHttpxRequestUrls())


def parse_retry_after(value: str | None) -> float | None:
    """### Parse a `Retry-After` header into a bounded number of seconds."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        pass
    else:
        return max(0.0, seconds)
    try:
        target = parsedate_to_datetime(value.strip())
    except (TypeError, ValueError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return max(0.0, (target - datetime.now(tz=UTC)).total_seconds())


def retry_delay(attempt: int, *, backoff_seconds: float, retry_after_seconds: float | None = None) -> float:
    """### Choose a jittered backoff, honouring a server-directed hint when present."""
    if retry_after_seconds is not None:
        return min(retry_after_seconds, 60.0)
    base = backoff_seconds * (2.0 ** max(0, attempt - 1))
    return min(base * (1 + random.random() * 0.25), 60.0)


class HostPacer:
    """### Enforce a minimum delay between requests to each host.

    Pacing is keyed by hostname rather than by fetcher, so strategies **within one process**
    share a cadence. It is not a cross-process rate limiter: separate worker containers each
    pace independently. Access is serialized with a lock because the shared pacer may be used
    from more than one thread (for example the worker loop and its heartbeat).
    """

    def __init__(
        self, *, sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        """### Store the sleep/clock hooks used to space requests."""
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: dict[str, float] = {}
        self._lock = Lock()

    def wait_for_slot(self, host: str, delay_seconds: float) -> None:
        """### Sleep just long enough to honour the per-host request spacing."""
        if delay_seconds <= 0:
            return
        with self._lock:
            now = self._monotonic()
            last = self._last_request_at.get(host)
            if last is not None:
                remaining = delay_seconds - (now - last)
                if remaining > 0:
                    self._sleep(remaining)
            self._last_request_at[host] = self._monotonic()


class LruCache[K, V]:
    """### A tiny bounded LRU map so caches cannot grow without limit."""

    def __init__(self, max_entries: int) -> None:
        """### Bound the cache to a fixed number of entries (0 disables caching)."""
        self._max_entries = max(0, max_entries)
        self._store: OrderedDict[K, V] = OrderedDict()

    def get(self, key: K) -> V | None:
        """### Return a cached value and mark it recently used."""
        value = self._store.get(key)
        if value is None:
            return None
        self._store.move_to_end(key)
        return value

    def put(self, key: K, value: V) -> None:
        """### Insert a value, evicting the least-recently-used entry when full."""
        if self._max_entries <= 0:
            return
        self._store[key] = value
        self._store.move_to_end(key)
        while len(self._store) > self._max_entries:
            self._store.popitem(last=False)


class HttpFetcher:
    """### Fetch pages over HTTP while respecting timeouts, pacing, and bounded retries."""

    engine_name = "http"

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.5,
        request_delay_seconds: float = 0.0,
        max_response_bytes: int = 26_214_400,
        allowed_redirect_hosts: tuple[str, ...] = (),
        allowed_hosts: tuple[str, ...] = (),
        allowed_suffixes: tuple[str, ...] = (),
        enable_cache: bool = True,
        max_cache_entries: int = 256,
        max_redirects: int = 5,
        client: httpx.Client | None = None,
        host_pacer: HostPacer | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """### Build a fetcher with an injectable client and clock."""
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._max_response_bytes = max(1, max_response_bytes)
        self._allowed_redirect_hosts = tuple(host.lower().rstrip(".") for host in allowed_redirect_hosts)
        self._allowed_hosts = tuple(host.lower().rstrip(".") for host in allowed_hosts)
        self._allowed_suffixes = tuple(suffix.lower().rstrip(".") for suffix in allowed_suffixes)
        self._enable_cache = enable_cache
        self._max_redirects = max(0, max_redirects)
        self._sleep = sleep
        self._request_delay_seconds = max(0.0, request_delay_seconds)
        self._pacer = host_pacer or HostPacer(sleep=sleep, monotonic=monotonic)
        self._cache: LruCache[tuple[str, bool, int], FetchedPage] = LruCache(max_cache_entries)
        self._logger = get_logger(__name__)
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "ar,en;q=0.9",
                # $ Only well-supported encodings, so proxy edge cases cannot corrupt a body.$
                "Accept-Encoding": "gzip, deflate",
            },
        )
        self._owns_client = client is None

    def close(self) -> None:
        """### Release the underlying HTTP connection pool."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> HttpFetcher:
        """### Support use as a context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """### Close the client when leaving the context."""
        self.close()

    def fetch(self, url: str) -> FetchedPage:
        """### Retrieve one URL as a textual page, caching repeats within a run."""
        return self._fetch_cached(url, require_text=True)

    def fetch_binary(self, url: str, *, max_bytes: int | None = None) -> FetchedPage:
        """### Retrieve one URL as raw bytes, for payloads such as PDFs."""
        return self._fetch_cached(url, require_text=False, max_bytes=max_bytes)

    def post_json(self, url: str, *, payload: str) -> FetchedPage:
        """### Send a JSON body to one URL and return the response page."""
        return self._request_with_retries(
            "POST",
            url,
            require_text=True,
            max_bytes=self._max_response_bytes,
            content=payload.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def _fetch_cached(self, url: str, *, require_text: bool, max_bytes: int | None = None) -> FetchedPage:
        """### Fetch a URL once per run, applying the retry policy.

        Only textual responses are cached: binary payloads (PDFs and other
        attachments) are large and rarely re-requested, so caching them would
        hold tens of megabytes in memory for the whole run.
        """
        limit = min(max_bytes, self._max_response_bytes) if max_bytes is not None else self._max_response_bytes
        if not (self._enable_cache and require_text):
            return self._request_with_retries("GET", url, require_text=require_text, max_bytes=limit)
        cache_key = (url, require_text, limit)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._logger.debug("fetch_cache_hit", extra={"url": redact_url(validate_http_url(url))})
            return cached
        page = self._request_with_retries("GET", url, require_text=require_text, max_bytes=limit)
        self._cache.put(cache_key, page)
        return page

    def _request_with_retries(
        self,
        method: str,
        url: str,
        *,
        require_text: bool,
        max_bytes: int,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> FetchedPage:
        """### Perform a request with bounded retries for transient failures."""

        def wait(retry_state: object) -> float:
            """### Choose jittered backoff while honoring a server retry hint."""
            attempt = getattr(retry_state, "attempt_number", 1)
            outcome = getattr(retry_state, "outcome", None)
            error = outcome.exception() if outcome is not None else None
            retry_after = error.retry_after_seconds if isinstance(error, TransientFetchError) else None
            return retry_delay(attempt, backoff_seconds=self._backoff_seconds, retry_after_seconds=retry_after)

        retryer = Retrying(
            retry=retry_if_exception_type(TransientFetchError),
            stop=stop_after_attempt(self._max_retries + 1),
            wait=wait,
            sleep=self._sleep,
            reraise=True,
        )
        for attempt in retryer:
            with attempt:
                return self._request_streamed(
                    method,
                    url,
                    require_text=require_text,
                    max_bytes=max_bytes,
                    content=content,
                    headers=headers,
                )
        raise TransientFetchError("retry loop exhausted without a response", url=url)

    def _request_streamed(
        self,
        method: str,
        url: str,
        *,
        require_text: bool,
        max_bytes: int,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> FetchedPage:
        """### Stream a bounded response and validate every redirect before following it."""
        validate_http_url(url, allowed_hosts=self._allowed_hosts, allowed_suffixes=self._allowed_suffixes)
        original_host = urlparse(url).hostname or ""
        allowed_hosts = (*self._allowed_redirect_hosts, original_host.lower().rstrip("."))
        current_url = url
        current_method = method
        current_content = content
        for redirect_count in range(self._max_redirects + 1):
            page, location = self._request_once(
                current_method,
                current_url,
                require_text=require_text,
                max_bytes=max_bytes,
                content=current_content,
                headers=headers,
            )
            if page.status_code not in {301, 302, 303, 307, 308}:
                return page
            if not location:
                raise FetchError(
                    "redirect response has no Location header", url=current_url, status_code=page.status_code
                )
            if redirect_count == self._max_redirects:
                raise FetchError("redirect limit exceeded", url=current_url, status_code=page.status_code)
            destination = validate_http_url(urljoin(current_url, location), allowed_hosts=allowed_hosts)
            if page.status_code == 303 or (page.status_code in {301, 302} and current_method == "POST"):
                current_method, current_content, headers = "GET", None, None
            current_url = destination
        raise FetchError("redirect limit exceeded", url=current_url)

    def _request_once(
        self,
        method: str,
        url: str,
        *,
        require_text: bool,
        max_bytes: int,
        content: bytes | None,
        headers: dict[str, str] | None,
    ) -> tuple[FetchedPage, str | None]:
        """### Issue one paced streaming request with a strict body-size limit."""
        self._pacer.wait_for_slot(urlparse(url).hostname or "", self._request_delay_seconds)
        try:
            with self._client.stream(method, url, content=content, headers=headers) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    page = FetchedPage(
                        str(response.url), response.status_code, b"", response.headers.get("content-type")
                    )
                    return page, response.headers.get("location")
                declared_length = response.headers.get("content-length")
                if declared_length and declared_length.isdigit() and int(declared_length) > max_bytes:
                    raise ResponseTooLargeError(
                        f"response exceeds the {max_bytes} byte download limit",
                        url=url,
                        status_code=response.status_code,
                    )
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes(chunk_size=65536):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ResponseTooLargeError(
                            f"response exceeds the {max_bytes} byte download limit",
                            url=url,
                            status_code=response.status_code,
                        )
                    chunks.append(chunk)
                buffered = self._buffered_response(response, b"".join(chunks))
        except httpx.TimeoutException as error:
            raise TransientFetchError(f"timeout after {self._timeout_seconds}s: {error}", url=url) from error
        except httpx.TransportError as error:
            raise TransientFetchError(f"transport error: {error}", url=url) from error
        except httpx.HTTPError as error:
            # $ A malformed response (bad compression, protocol failure) becomes a typed error.$
            raise FetchError(f"invalid HTTP response: {error}", url=url) from error
        return self._translate(buffered, url, require_text=require_text), None

    @staticmethod
    def _buffered_response(response: httpx.Response, content: bytes) -> httpx.Response:
        """### Rebuild a response from decoded content without stale body framing headers."""
        headers = [
            (key, value) for key, value in response.headers.multi_items() if key.lower() not in _DECOMPRESSED_HEADERS
        ]
        return httpx.Response(
            response.status_code,
            headers=headers,
            content=content,
            request=response.request,
            extensions=response.extensions,
        )

    def _translate(self, response: httpx.Response, url: str, *, require_text: bool = True) -> FetchedPage:
        """### Convert an HTTP response into a page or a typed failure."""
        block_reason = detect_bot_block(response.status_code, response.content)
        if block_reason is not None:
            raise AccessBlockedError(
                f"source refused the request ({block_reason})",
                url=url,
                status_code=response.status_code,
                reason=block_reason,
            )
        if response.status_code in RETRYABLE_STATUS_CODES:
            raise TransientFetchError(
                f"retryable status {response.status_code}",
                url=url,
                status_code=response.status_code,
                retry_after_seconds=parse_retry_after(response.headers.get("retry-after")),
            )
        if response.status_code in {401, 403}:
            raise AccessDeniedError(
                f"access denied with status {response.status_code}", url=url, status_code=response.status_code
            )
        if response.status_code >= 400:
            raise FetchError(f"unexpected status {response.status_code}", url=url, status_code=response.status_code)

        content_type = response.headers.get("content-type")
        if require_text and content_type and not any(token in content_type.lower() for token in _TEXT_CONTENT_TYPES):
            raise NonTextContentError(
                f"unusable content type {content_type}", url=url, status_code=response.status_code
            )

        return FetchedPage(
            url=str(response.url),
            status_code=response.status_code,
            content=response.content,
            content_type=content_type,
        )
