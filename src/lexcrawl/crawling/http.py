"""### HTTP crawling strategy built on httpx, with pacing and bounded retries."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from lexcrawl.crawling.base import (
    RETRYABLE_STATUS_CODES,
    AccessBlockedError,
    FetchedPage,
    FetchError,
    NonTextContentError,
    TransientFetchError,
    detect_bot_block,
)
from lexcrawl.crawling.pacing import RequestPacer
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
        self._sleep = sleep
        self._pacer = RequestPacer(request_delay_seconds, sleep=sleep, monotonic=monotonic)
        self._cache: dict[tuple[str, bool], FetchedPage] = {}
        self._logger = get_logger(__name__)
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=True,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "ar,en;q=0.9",
            },
        )
        self._owns_client = client is None
        self._robots = RobotsPolicy(self._read_robots_file, user_agent=user_agent)

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

    def fetch_binary(self, url: str) -> FetchedPage:
        """### Retrieve one URL as raw bytes, for payloads such as PDFs."""
        return self._fetch_cached(url, require_text=False)

    def post_json(self, url: str, *, payload: str) -> FetchedPage:
        """### Send a JSON body to one URL and return the response page."""
        if self._respect_robots_txt:
            self._robots.assert_allowed(url)
        return self._post_once_with_retries(url, payload=payload)

    def _post_once_with_retries(self, url: str, *, payload: str) -> FetchedPage:
        """### Post a payload with bounded retries for transient failures."""
        retryer = Retrying(
            retry=retry_if_exception_type(TransientFetchError),
            stop=stop_after_attempt(self._max_retries + 1),
            wait=wait_exponential(multiplier=self._backoff_seconds, min=self._backoff_seconds, max=30.0),
            sleep=self._sleep,
            reraise=True,
        )
        for attempt in retryer:
            with attempt:
                return self._post(url, payload=payload)
        raise TransientFetchError("retry loop exhausted without a response", url=url)

    def _post(self, url: str, *, payload: str) -> FetchedPage:
        """### Issue a single paced POST request and translate its outcome."""
        self._pacer.wait_for_slot()
        try:
            response = self._client.post(url, content=payload, headers={"Content-Type": "application/json"})
        except httpx.TimeoutException as error:
            raise TransientFetchError(f"timeout after {self._timeout_seconds}s: {error}", url=url) from error
        except httpx.TransportError as error:
            raise TransientFetchError(f"transport error: {error}", url=url) from error
        return self._translate(response, url)

    def _fetch_cached(self, url: str, *, require_text: bool) -> FetchedPage:
        """### Fetch a URL once per run, applying robots and retry policy."""
        cached = self._cache.get((url, require_text))
        if cached is not None:
            self._logger.debug("fetch_cache_hit", extra={"url": url})
            return cached
        if self._respect_robots_txt:
            self._robots.assert_allowed(url)
        page = self._fetch_once_with_retries(url, require_text=require_text)
        self._cache[(url, require_text)] = page
        return page

    def _fetch_once_with_retries(self, url: str, *, require_text: bool = True) -> FetchedPage:
        """### Perform the request with bounded retries for transient failures."""
        retryer = Retrying(
            retry=retry_if_exception_type(TransientFetchError),
            stop=stop_after_attempt(self._max_retries + 1),
            wait=wait_exponential(multiplier=self._backoff_seconds, min=self._backoff_seconds, max=30.0),
            sleep=self._sleep,
            reraise=True,
        )
        for attempt in retryer:
            with attempt:
                return self._request(url, require_text=require_text)
        raise TransientFetchError("retry loop exhausted without a response", url=url)

    def _request(self, url: str, *, require_text: bool = True) -> FetchedPage:
        """### Issue a single paced request and translate its outcome."""
        self._pacer.wait_for_slot()
        try:
            response = self._client.get(url)
        except httpx.TimeoutException as error:
            raise TransientFetchError(f"timeout after {self._timeout_seconds}s: {error}", url=url) from error
        except httpx.TransportError as error:
            raise TransientFetchError(f"transport error: {error}", url=url) from error

        return self._translate(response, url, require_text=require_text)

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
                f"retryable status {response.status_code}", url=url, status_code=response.status_code
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
            response = self._client.get(robots_url, timeout=self._timeout_seconds)
        except httpx.HTTPError as error:
            return RobotsFile(error=str(error))
        text = response.text if response.status_code == 200 else None
        return RobotsFile(status_code=response.status_code, text=text)
