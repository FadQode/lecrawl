"""### Browser crawling strategy for sources that need a rendered document."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
from urllib.parse import urlparse

from tenacity import Retrying, retry_if_exception_type, stop_after_attempt

from lexcrawl.crawling.base import (
    RETRYABLE_STATUS_CODES,
    AccessBlockedError,
    AccessDeniedError,
    FetchedPage,
    Fetcher,
    FetchError,
    ResponseTooLargeError,
    TransientFetchError,
    detect_bot_block,
    validate_http_url,
)
from lexcrawl.crawling.http import HttpFetcher
from lexcrawl.crawling.pacing import RequestPacer
from lexcrawl.crawling.retry import parse_retry_after, retry_delay
from lexcrawl.crawling.robots import RobotsFile, RobotsPolicy
from lexcrawl.logging import get_logger

if TYPE_CHECKING:
    from playwright.sync_api import Browser, BrowserContext, Page, Playwright

WaitUntil = Literal["domcontentloaded", "load", "networkidle"]


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """### Outcome of one browser navigation."""

    url: str
    status_code: int | None
    html: str
    retry_after: str | None = None


@runtime_checkable
class BrowserSession(Protocol):
    """### Browser driver interface used by the browser crawling strategy."""

    def render(self, url: str) -> RenderedPage:
        """### Navigate to a URL and return the rendered document."""
        ...

    def read_text(self, url: str) -> RobotsFile:
        """### Retrieve a plain-text resource without rendering it."""
        ...

    def close(self) -> None:
        """### Release the browser and its driver."""
        ...


class PlaywrightBrowserSession:
    """### Drive one Chromium instance through Playwright."""

    def __init__(
        self,
        *,
        headless: bool = True,
        timeout_seconds: float = 30.0,
        wait_until: WaitUntil = "domcontentloaded",
        settle_milliseconds: int = 250,
        user_agent: str | None = None,
        allowed_redirect_hosts: tuple[str, ...] = (),
        max_response_bytes: int = 26_214_400,
    ) -> None:
        """### Configure the browser launch and navigation behaviour."""
        self._headless = headless
        self._timeout_milliseconds = int(timeout_seconds * 1000)
        self._wait_until = wait_until
        self._settle_seconds = max(0, settle_milliseconds) / 1000
        self._user_agent = user_agent
        self._allowed_redirect_hosts = tuple(allowed_redirect_hosts)
        self._max_response_bytes = max_response_bytes
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._logger = get_logger(__name__)

    def render(self, url: str) -> RenderedPage:
        """### Navigate to a URL and return the rendered document."""
        page = self._ensure_page()
        blocked: list[str] = []

        def guard_navigation(route: Any) -> None:
            """### Abort sub-resources and off-host navigations before they leave the page."""
            request = route.request
            if request.resource_type in {"image", "media", "font"}:
                route.abort()
                return
            if request.is_navigation_request() or request.resource_type == "document":
                allowed = self._allowed_redirect_hosts or (urlparse(url).hostname or "",)
                try:
                    validate_http_url(
                        request.url,
                        allowed_hosts=allowed,
                        allow_private_hosts=self._allowed_redirect_hosts,
                    )
                except FetchError:
                    blocked.append(request.url)
                    route.abort()
                    return
            route.continue_()

        page.route("**/*", guard_navigation)
        page.add_init_script(
            f"""(() => {{
                    const check = () => {{
                    if (document.documentElement &&
                        document.documentElement.outerHTML.length > {self._max_response_bytes}) window.stop();
                }};
                new MutationObserver(check).observe(document, {{subtree: true, childList: true, attributes: true}});
            }})()"""
        )
        try:
            response = page.goto(url, timeout=self._timeout_milliseconds, wait_until=self._wait_until)
            if blocked:
                raise FetchError("browser navigation destination was rejected", url=blocked[-1])
        except Exception as error:  # $ Driver failures are transient from the strategy's point of view.$
            if blocked:
                raise FetchError("browser navigation destination was rejected", url=blocked[-1]) from error
            if isinstance(error, FetchError):
                raise
            raise TransientFetchError(f"browser navigation failed: {error}", url=url) from error
        finally:
            page.unroute("**/*", guard_navigation)
        if self._settle_seconds:
            page.wait_for_timeout(int(self._settle_seconds * 1000))
        status_code = response.status if response is not None else None
        retry_after = response.headers.get("retry-after") if response is not None else None
        final_url = page.url
        allowed = self._allowed_redirect_hosts or (urlparse(url).hostname or "",)
        try:
            validate_http_url(
                final_url,
                allowed_hosts=allowed,
                allow_private_hosts=self._allowed_redirect_hosts,
            )
        except FetchError as error:
            raise FetchError("browser navigation ended outside the requested host boundary", url=final_url) from error
        return RenderedPage(url=final_url, status_code=status_code, html=page.content(), retry_after=retry_after)

    def read_text(self, url: str) -> RobotsFile:
        """### Retrieve a plain-text resource without rendering it."""
        context = self._ensure_context()
        try:
            response = context.request.get(url, timeout=self._timeout_milliseconds)
        except Exception as error:  # $ The driver reports unreachable resources as exceptions.$
            return RobotsFile(error=str(error))
        allowed = self._allowed_redirect_hosts or (urlparse(url).hostname or "",)
        try:
            validate_http_url(
                response.url,
                allowed_hosts=allowed,
                allow_private_hosts=self._allowed_redirect_hosts,
            )
        except FetchError as error:
            return RobotsFile(status_code=response.status, error=str(error))
        content = response.body() if response.status == 200 else None
        if content is not None and len(content) > min(self._max_response_bytes, 512_000):
            return RobotsFile(status_code=response.status, error="robots.txt exceeded the byte limit")
        return RobotsFile(
            status_code=response.status,
            text=content.decode("utf-8-sig", errors="replace") if content is not None else None,
        )

    def close(self) -> None:
        """### Close the browser, its context, and the driver."""
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    def _ensure_page(self) -> Page:
        """### Return the shared page, launching the browser on first use."""
        if self._page is None:
            self._page = self._ensure_context().new_page()
        return self._page

    def _ensure_context(self) -> BrowserContext:
        """### Return the shared browser context, launching Chromium on first use."""
        if self._context is not None:
            return self._context
        from playwright.sync_api import sync_playwright

        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=self._headless)
        # $ An empty user agent keeps the browser's real identity instead of spoofing one.$
        context = browser.new_context(user_agent=self._user_agent)
        context.set_default_timeout(self._timeout_milliseconds)
        self._playwright, self._browser, self._context = playwright, browser, context
        self._logger.info("browser_started", extra={"headless": self._headless})
        return context


class BrowserFetcher:
    """### Fetch rendered pages while respecting timeouts, pacing, and robots."""

    engine_name = "browser"

    def __init__(
        self,
        *,
        user_agent: str,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        backoff_seconds: float = 1.5,
        request_delay_seconds: float = 0.0,
        respect_robots_txt: bool = True,
        allowed_redirect_hosts: tuple[str, ...] = (),
        max_response_bytes: int = 26_214_400,
        headless: bool = True,
        wait_until: WaitUntil = "domcontentloaded",
        settle_milliseconds: int = 250,
        session: BrowserSession | None = None,
        binary_fetcher: Fetcher | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """### Build a browser fetcher with an injectable session and clock."""
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep
        self._respect_robots_txt = respect_robots_txt
        self._allowed_redirect_hosts = tuple(allowed_redirect_hosts)
        self._max_response_bytes = max(1, max_response_bytes)
        self._pacer = RequestPacer(request_delay_seconds, sleep=sleep, monotonic=monotonic)
        self._cache: dict[str, FetchedPage] = {}
        self._owner_thread: int | None = None
        self._logger = get_logger(__name__)
        self._owns_session = session is None
        self._session: BrowserSession = session or PlaywrightBrowserSession(
            headless=headless,
            timeout_seconds=timeout_seconds,
            wait_until=wait_until,
            settle_milliseconds=settle_milliseconds,
            user_agent=user_agent or None,
            allowed_redirect_hosts=allowed_redirect_hosts,
            max_response_bytes=max_response_bytes,
        )
        self._owns_binary_fetcher = binary_fetcher is None
        self._binary_fetcher = binary_fetcher or HttpFetcher(
            user_agent=user_agent,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            request_delay_seconds=request_delay_seconds,
            respect_robots_txt=respect_robots_txt,
            allowed_redirect_hosts=allowed_redirect_hosts,
            max_response_bytes=self._max_response_bytes,
        )
        self._robots = RobotsPolicy(
            self._session.read_text,
            user_agent=user_agent,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
        )

    def close(self) -> None:
        """### Close the browser session when this fetcher owns it."""
        if self._owns_session:
            self._session.close()
        if self._owns_binary_fetcher:
            self._binary_fetcher.close()

    def __enter__(self) -> BrowserFetcher:
        """### Support use as a context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """### Close the session when leaving the context."""
        self.close()

    def fetch_binary(self, url: str, *, max_bytes: int | None = None) -> FetchedPage:
        """### Retrieve raw bytes without rendering, for payloads such as PDFs."""
        self._assert_single_thread()
        limit = self._max_response_bytes if max_bytes is None else min(max_bytes, self._max_response_bytes)
        return self._binary_fetcher.fetch_binary(url, max_bytes=limit)

    def post_json(self, url: str, *, payload: str) -> FetchedPage:
        """### Refuse to post JSON, because a rendering engine cannot serve a feed."""
        raise FetchError("the browser engine cannot post JSON payloads; use the http engine", url=url)

    def fetch(self, url: str) -> FetchedPage:
        """### Render one URL as a page, caching repeats within a run."""
        self._assert_single_thread()
        cached = self._cache.get(url)
        if cached is not None:
            self._logger.debug("fetch_cache_hit", extra={"url": url})
            return cached
        validate_http_url(
            url,
            allowed_hosts=self._allowed_redirect_hosts,
            allow_private_hosts=self._allowed_redirect_hosts,
        )
        if self._respect_robots_txt:
            self._robots.assert_allowed(url)
        page = self._fetch_once_with_retries(url)
        self._cache[url] = page
        return page

    def _assert_single_thread(self) -> None:
        """### Refuse cross-thread use, which the browser driver does not support."""
        current = threading.get_ident()
        if self._owner_thread is None:
            self._owner_thread = current
            return
        if current != self._owner_thread:
            raise RuntimeError("BrowserFetcher must run in one thread; keep source concurrency at 1")

    def _fetch_once_with_retries(self, url: str) -> FetchedPage:
        """### Render the page with bounded retries for transient failures."""

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
                return self._render(url)
        raise TransientFetchError("retry loop exhausted without a response", url=url)

    def _render(self, url: str) -> FetchedPage:
        """### Render one page and translate its outcome into the fetch contract."""
        self._pacer.wait_for_slot()
        rendered = self._session.render(url)
        content = rendered.html.encode("utf-8")
        if len(content) > self._max_response_bytes:
            raise ResponseTooLargeError(
                f"rendered document exceeds the {self._max_response_bytes} byte limit",
                url=url,
                status_code=rendered.status_code,
            )

        block_reason = detect_bot_block(rendered.status_code, content)
        if block_reason is not None:
            raise AccessBlockedError(
                f"source refused the request ({block_reason})",
                url=url,
                status_code=rendered.status_code or 0,
                reason=block_reason,
            )
        if rendered.status_code in RETRYABLE_STATUS_CODES:
            raise TransientFetchError(
                f"retryable status {rendered.status_code}",
                url=url,
                status_code=rendered.status_code,
                retry_after_seconds=parse_retry_after(rendered.retry_after),
            )
        if rendered.status_code in {401, 403}:
            raise AccessDeniedError(
                f"access denied with status {rendered.status_code}", url=url, status_code=rendered.status_code
            )
        if rendered.status_code is not None and rendered.status_code >= 400:
            raise FetchError(f"unexpected status {rendered.status_code}", url=url, status_code=rendered.status_code)
        if not rendered.html.strip():
            raise FetchError("browser returned an empty document", url=url, status_code=rendered.status_code)

        try:
            allowed = self._allowed_redirect_hosts or (urlparse(url).hostname or "",)
            validate_http_url(rendered.url, allowed_hosts=allowed, allow_private_hosts=self._allowed_redirect_hosts)
        except FetchError as error:
            raise FetchError(f"browser navigation left the requested host: {error}", url=url) from error
        return FetchedPage(
            url=rendered.url,
            status_code=rendered.status_code,
            content=content,
            content_type="text/html",
        )
