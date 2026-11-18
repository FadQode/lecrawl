"""### Direct HTML acquisition with standard navigation headers, failing closed on a challenge."""

from __future__ import annotations

import time
from collections.abc import Callable
from urllib.parse import urljoin

import httpx

from lexcrawl.crawling.base import FetchError, detect_bot_block, validate_http_url
from lexcrawl.crawling.scrape import ScrapeChallengeError, ScrapedPage, ScrapeError
from lexcrawl.logging import get_logger

LOGGER = get_logger(__name__)

# $ Standard headers every browser navigation sends; no identity is spoofed and no challenge is solved.$
NAVIGATION_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

_REDIRECT_STATUS_CODES = frozenset({301, 302, 303, 307, 308})


def _is_challenge(status_code: int, content: bytes) -> bool:
    """### Report whether a response is an access challenge the caller must not retry around."""
    if status_code == 403:
        return True
    return detect_bot_block(status_code, content) is not None


class DirectHtmlScraper:
    """### Acquire HTML directly over HTTP, fail closed on a challenge."""

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 30.0,
        retry_delay_seconds: float = 2.0,
        max_attempts: int = 2,
        max_redirects: int = 5,
        allowed_hosts: tuple[str, ...] = (),
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """### Configure the identifying user agent, one retry, and the redirect allowlist."""
        self._max_attempts = max(1, max_attempts)
        self._retry_delay_seconds = max(0.0, retry_delay_seconds)
        self._max_redirects = max(0, max_redirects)
        self._allowed_hosts = tuple(host.lower().rstrip(".") for host in allowed_hosts)
        self._sleep = sleep
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            headers={**NAVIGATION_HEADERS, "User-Agent": user_agent},
        )
        self._owns_client = client is None

    def close(self) -> None:
        """### Release the underlying HTTP connection pool."""
        if self._owns_client:
            self._client.close()

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Acquire one URL, retrying once, then raising so the caller can fall back."""
        del cache  # $ A direct fetch is cheap, so responses are not cached.$
        self._validate(url)
        last_error: ScrapeError | None = None
        for attempt in range(self._max_attempts):
            if attempt:
                # $ A single retry after a short delay absorbs a transient block; never a variation.$
                self._sleep(self._retry_delay_seconds)
            try:
                return self._acquire(url)
            except ScrapeChallengeError as error:
                last_error = error
            except ScrapeError as error:
                # $ Only transient failures are retried; anything else fails immediately.$
                if not error.retryable:
                    raise
                last_error = error
        assert last_error is not None
        raise last_error

    def store(self, url: str, page: ScrapedPage) -> None:
        """### Direct fetching has no persistent cache."""
        return None

    def evict(self, url: str) -> None:
        """### Direct fetching has no persistent cache."""
        return None

    def _acquire(self, url: str) -> ScrapedPage:
        """### Follow in-scope redirects, failing closed on a challenge response."""
        current = url
        for _ in range(self._max_redirects + 1):
            response = self._get(current)
            if response.status_code in _REDIRECT_STATUS_CODES:
                location = response.headers.get("location")
                if not location:
                    raise ScrapeError(
                        "redirect response has no Location header", url=current, status_code=response.status_code
                    )
                current = self._validate(urljoin(current, location))
                continue
            if _is_challenge(response.status_code, response.content):
                raise ScrapeChallengeError(
                    f"source returned a challenge (HTTP {response.status_code}); failing closed",
                    url=current,
                    status_code=response.status_code,
                )
            return ScrapedPage(
                requested_url=str(response.url),
                provider_status=response.status_code,
                target_status=response.status_code,
                html=response.text,
                markdown="",
                credits_used=0,
            )
        raise ScrapeError("redirect limit exceeded", url=url)

    def _get(self, url: str) -> httpx.Response:
        """### Issue one request and translate transport failures into typed errors."""
        try:
            return self._client.get(url)
        except httpx.TimeoutException as error:
            raise ScrapeError(f"direct fetch timed out: {error}", url=url, retryable=True) from error
        except httpx.TransportError as error:
            raise ScrapeError(f"direct fetch transport error: {error}", url=url, retryable=True) from error
        except httpx.HTTPError as error:
            raise ScrapeError(f"direct fetch failed: {error}", url=url) from error

    def _validate(self, url: str) -> str:
        """### Validate a destination against the host allowlist."""
        try:
            return validate_http_url(url, allowed_hosts=self._allowed_hosts)
        except FetchError as error:
            raise ScrapeError(str(error), url=url) from error
