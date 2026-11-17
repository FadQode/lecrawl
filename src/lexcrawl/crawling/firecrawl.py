"""### Minimal Firecrawl client used by the Commerce reports actor."""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from lexcrawl.crawling.scrape import ScrapedPage, ScrapeError, Scraper
from lexcrawl.logging import get_logger

LOGGER = get_logger(__name__)

# $ The acquisition contract is provider-neutral; this module supplies the Firecrawl client.$
FirecrawlError = ScrapeError
FirecrawlScraper = Scraper

DEFAULT_API_URL = "https://api.firecrawl.dev/v2/scrape"
TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
AUTH_STATUS_CODES = frozenset({401, 402})
PERMANENT_STATUS_CODES = frozenset({400, 403, 404, 422})
MAX_BACKOFF_SECONDS = 30.0


class FirecrawlAuthError(ScrapeError):
    """### Raised when Firecrawl rejects the credentials or the plan."""


class FirecrawlClient:
    """### Sequential, budget-aware Firecrawl v2 scrape client."""

    def __init__(
        self,
        *,
        api_key: str,
        api_url: str = DEFAULT_API_URL,
        timeout_seconds: float = 90.0,
        max_retries: int = 2,
        request_delay_seconds: float = 0.0,
        cache_dir: str | None = None,
        cache_ttl_seconds: float = 7 * 24 * 3600,
        max_response_bytes: int = 26_214_400,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """### Configure credentials, retries, pacing, and the optional response cache."""
        self._api_key = api_key.strip()
        self._api_url = api_url
        self._max_retries = max(0, max_retries)
        self._request_delay_seconds = max(0.0, request_delay_seconds)
        self._sleep = sleep
        self._cache_dir = Path(cache_dir) if cache_dir else None
        self._cache_ttl_seconds = max(0.0, cache_ttl_seconds)
        self._max_response_bytes = max(1, max_response_bytes)
        # $ The provider can send an undecodable gzip header, so ask for identity encoding.$
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            headers={"Accept-Encoding": "identity"},
        )
        self._owns_client = client is None

    def close(self) -> None:
        """### Close the underlying HTTP client when this instance owns it."""
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> FirecrawlClient:
        """### Support use as a context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """### Close the client when leaving the context."""
        self.close()

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Scrape a URL, optionally reusing a cached successful response."""
        cached = self._read_cache(url) if cache else None
        if cached is not None:
            LOGGER.info("firecrawl_cache_hit", extra={"url": url})
            return cached
        page = self._scrape_with_retries(url)
        # $ Never cache error/challenge/thin pages, or a retry would replay the failure forever.$
        cacheable = (page.target_status is None or page.target_status < 400) and bool(page.html.strip())
        if cache and cacheable:
            self._write_cache(url, page)
        return page

    def _scrape_with_retries(self, url: str) -> ScrapedPage:
        """### Issue the request with bounded exponential backoff for transient failures."""
        last_error: FirecrawlError | None = None
        for attempt in range(self._max_retries + 1):
            if self._request_delay_seconds and attempt == 0:
                self._sleep(self._request_delay_seconds)
            try:
                return self._request(url)
            except FirecrawlError as error:
                if not error.retryable:
                    raise
                last_error = error
                if attempt < self._max_retries:
                    # $ Honour Retry-After when present, else exponential backoff, bounded.$
                    delay = (
                        error.retry_after_seconds
                        if error.retry_after_seconds is not None
                        else (self._request_delay_seconds * (2**attempt) or 1.0)
                    )
                    self._sleep(min(MAX_BACKOFF_SECONDS, delay))
        assert last_error is not None
        raise last_error

    def _request(self, url: str) -> ScrapedPage:
        """### Perform one scrape request and translate its outcome."""
        status_code, raw, retry_after = self._send(url)
        if status_code in AUTH_STATUS_CODES:
            raise FirecrawlAuthError(
                f"firecrawl rejected the credentials with status {status_code}",
                url=url,
                status_code=status_code,
            )
        if status_code in PERMANENT_STATUS_CODES:
            raise FirecrawlError(
                f"firecrawl rejected the request with status {status_code}",
                url=url,
                status_code=status_code,
            )
        if status_code in TRANSIENT_STATUS_CODES:
            raise FirecrawlError(
                f"firecrawl transient status {status_code}",
                url=url,
                status_code=status_code,
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if status_code >= 400:
            raise FirecrawlError(f"firecrawl unexpected status {status_code}", url=url, status_code=status_code)

        payload = self._payload(raw, url)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise FirecrawlError("firecrawl response has no data object", url=url)
        raw_metadata = data.get("metadata")
        metadata: dict[str, object] = raw_metadata if isinstance(raw_metadata, dict) else {}
        raw_status = metadata.get("statusCode")
        raw_credits = metadata.get("creditsUsed")
        target_status = raw_status if isinstance(raw_status, int) and not isinstance(raw_status, bool) else None
        credits = raw_credits if isinstance(raw_credits, int) and not isinstance(raw_credits, bool) else 0
        return ScrapedPage(
            requested_url=url,
            provider_status=status_code,
            target_status=target_status,
            html=str(data.get("html") or ""),
            markdown=str(data.get("markdown") or ""),
            credits_used=credits,
        )

    def _send(self, url: str) -> tuple[int, bytes, float | None]:
        """### Send the scrape request and read the raw body without auto-decoding."""
        request = self._client.build_request(
            "POST",
            self._api_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"url": url, "formats": ["html", "markdown"]},
        )
        try:
            response = self._client.send(request, stream=True)
        except httpx.TimeoutException as error:
            raise FirecrawlError(f"firecrawl timeout: {error}", url=url, retryable=True) from error
        except httpx.TransportError as error:
            raise FirecrawlError(f"firecrawl transport error: {error}", url=url, retryable=True) from error
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in response.iter_raw():
                total += len(chunk)
                if total > self._max_response_bytes:
                    raise FirecrawlError(
                        f"firecrawl response exceeds the {self._max_response_bytes}-byte limit", url=url
                    )
                chunks.append(chunk)
        except httpx.HTTPError as error:
            raise FirecrawlError(f"firecrawl response could not be read: {error}", url=url, retryable=True) from error
        finally:
            response.close()
        raw = b"".join(chunks)
        # $ The provider can announce gzip on a plain body, so decode on the payload itself.$
        if raw[:2] == b"\x1f\x8b":
            try:
                raw = gzip.decompress(raw)
            except OSError as error:
                raise FirecrawlError(f"firecrawl gzip body is invalid: {error}", url=url) from error
        return response.status_code, raw, _parse_retry_after(response.headers.get("retry-after"))

    @staticmethod
    def _payload(raw: bytes, url: str) -> dict[str, object]:
        """### Parse the provider envelope, rejecting unusable responses."""
        try:
            payload = json.loads(raw)
        except ValueError as error:
            raise FirecrawlError(f"firecrawl response is not JSON: {error}", url=url) from error
        if not isinstance(payload, dict):
            raise FirecrawlError("firecrawl response is not a JSON object", url=url)
        if payload.get("success") is not True:
            message = payload.get("error") or payload.get("message") or "unknown provider error"
            raise FirecrawlError(f"firecrawl reported failure: {message}", url=url)
        return payload

    def _cache_path(self, url: str) -> Path | None:
        """### Return the cache file for a URL when caching is enabled."""
        if self._cache_dir is None:
            return None
        return self._cache_dir / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"

    def _read_cache(self, url: str) -> ScrapedPage | None:
        """### Load a previously cached successful scrape."""
        path = self._cache_path(url)
        if path is None or not path.is_file():
            return None
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(stored, dict) or stored.get("url") != url:
            return None
        if self._cache_ttl_seconds > 0:
            fetched_at = stored.get("fetched_at")
            if not isinstance(fetched_at, (int, float)) or (time.time() - float(fetched_at)) > self._cache_ttl_seconds:
                return None
        return ScrapedPage(
            requested_url=url,
            provider_status=int(stored.get("provider_status") or 200),
            target_status=stored.get("target_status"),
            html=str(stored.get("html") or ""),
            markdown=str(stored.get("markdown") or ""),
            credits_used=0,
            cached=True,
        )

    def _write_cache(self, url: str, page: ScrapedPage) -> None:
        """### Persist a successful scrape so reruns do not spend credits again."""
        path = self._cache_path(url)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "url": url,
                    "provider_status": page.provider_status,
                    "target_status": page.target_status,
                    "html": page.html,
                    "markdown": page.markdown,
                    "fetched_at": time.time(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def _parse_retry_after(value: str | None) -> float | None:
    """### Parse a Retry-After header (delta-seconds) into seconds."""
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        return None
