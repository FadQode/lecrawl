"""### HTTP crawling strategy built on httpx, with pacing and bounded retries."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
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
from lexcrawl.crawling.pacing import RequestPacer
from lexcrawl.crawling.retry import parse_retry_after, retry_delay
from lexcrawl.crawling.robots import RobotsFile, RobotsPolicy
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


class _RedactHttpxRequestUrls(logging.Filter):
    """Remove credential query values from HTTPX request log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_url(str(value)) if isinstance(value, httpx.URL) else value for value in record.args
            )
        return True


_httpx_logger = logging.getLogger("httpx")
if not any(isinstance(item, _RedactHttpxRequestUrls) for item in _httpx_logger.filters):
    _httpx_logger.addFilter(_RedactHttpxRequestUrls())


class HttpFetcher:
    """### Fetch pages over HTTP while respecting timeouts, pacing, and robots."""

    engine_name = "http"

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.5,
        request_delay_seconds: float = 0.0,
        respect_robots_txt: bool = True,
        max_response_bytes: int = 26_214_400,
        allowed_redirect_hosts: tuple[str, ...] = (),
        max_redirects: int = 5,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """### Build a fetcher with an injectable client and clock."""
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._respect_robots_txt = respect_robots_txt
        self._max_response_bytes = max(1, max_response_bytes)
        self._allowed_redirect_hosts = tuple(host.lower().rstrip(".") for host in allowed_redirect_hosts)
        self._max_redirects = max(0, max_redirects)
        self._sleep = sleep
        self._pacer = RequestPacer(request_delay_seconds, sleep=sleep, monotonic=monotonic)
        self._cache: dict[tuple[str, bool, int], FetchedPage] = {}
        self._logger = get_logger(__name__)
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "ar,en;q=0.9",
            },
        )
        self._owns_client = client is None
        self._robots = RobotsPolicy(
            self._read_robots_file,
            user_agent=user_agent,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
        )

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
        if self._respect_robots_txt:
            self._robots.assert_allowed(url)
        return self._post_once_with_retries(url, payload=payload)

    def _post_once_with_retries(self, url: str, *, payload: str) -> FetchedPage:
        """### Post a payload with bounded retries for transient failures."""

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
                return self._post(url, payload=payload)
        raise TransientFetchError("retry loop exhausted without a response", url=url)

    def _post(self, url: str, *, payload: str) -> FetchedPage:
        """### Issue a single paced POST request and translate its outcome."""
        return self._request_streamed(
            "POST",
            url,
            require_text=True,
            max_bytes=self._max_response_bytes,
            content=payload.encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    def _fetch_cached(self, url: str, *, require_text: bool, max_bytes: int | None = None) -> FetchedPage:
        """### Fetch a URL once per run, applying robots and retry policy."""
        limit = min(max_bytes, self._max_response_bytes) if max_bytes is not None else self._max_response_bytes
        cache_key = (url, require_text, limit)
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._logger.debug("fetch_cache_hit", extra={"url": redact_url(validate_http_url(url))})
            return cached
        if self._respect_robots_txt:
            self._robots.assert_allowed(url)
        page = self._fetch_once_with_retries(url, require_text=require_text, max_bytes=limit)
        self._cache[cache_key] = page
        return page

    def _fetch_once_with_retries(self, url: str, *, require_text: bool = True, max_bytes: int) -> FetchedPage:
        """### Perform the request with bounded retries for transient failures."""

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
                return self._request_streamed("GET", url, require_text=require_text, max_bytes=max_bytes)
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
        validate_http_url(url)
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
        self._pacer.wait_for_slot()
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
                buffered = httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    content=b"".join(chunks),
                    request=response.request,
                    extensions=response.extensions,
                )
        except httpx.TimeoutException as error:
            raise TransientFetchError(f"timeout after {self._timeout_seconds}s: {error}", url=url) from error
        except httpx.TransportError as error:
            raise TransientFetchError(f"transport error: {error}", url=url) from error
        return self._translate(buffered, url, require_text=require_text), None

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

    def _read_robots_file(self, robots_url: str) -> RobotsFile:
        """### Retrieve a host's robots.txt through the HTTP client."""
        try:
            page = self._request_streamed(
                "GET", robots_url, require_text=True, max_bytes=min(self._max_response_bytes, 512_000)
            )
        except FetchError as error:
            return RobotsFile(
                status_code=error.status_code,
                error=str(error),
                retry_after_seconds=getattr(error, "retry_after_seconds", None),
            )
        text = page.content.decode("utf-8-sig", errors="replace") if page.status_code == 200 else None
        return RobotsFile(status_code=page.status_code, text=text)
