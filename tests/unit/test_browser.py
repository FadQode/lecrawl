"""### Browser crawling strategy tests using an injected browser session."""

from __future__ import annotations

import threading

import pytest

from lexcrawl.crawling.base import (
    AccessBlockedError,
    FetchedPage,
    FetchError,
    ResponseTooLargeError,
    RobotsDisallowedError,
    TransientFetchError,
)
from lexcrawl.crawling.browser import BrowserFetcher, RenderedPage
from lexcrawl.crawling.robots import RobotsFile

from ..conftest import ClockRecorder

SITE = "https://listing.example.test"
ARTICLE_URL = f"{SITE}/ar/news/example-article"
OTHER_URL = f"{SITE}/ar/news/second-article"

OK_HTML = "<html lang='ar'><div class='item_'>الخبر</div></html>"
BLOCK_HTML = (
    "<html><head><title>Attention Required! | Cloudflare</title></head><body>you have been blocked</body></html>"
)
ALLOW_ALL = RobotsFile(status_code=200, text="User-agent: *\nDisallow:\n")


class FakeBrowserSession:
    """### Serve canned rendered pages without launching a browser."""

    def __init__(
        self,
        pages: dict[str, RenderedPage],
        *,
        robots: RobotsFile = ALLOW_ALL,
        failures: dict[str, Exception] | None = None,
    ) -> None:
        """### Map URLs to rendered pages and to failures that must be raised."""
        self._pages = pages
        self._robots = robots
        self._failures = failures or {}
        self.rendered: list[str] = []
        self.closed = False
        self.binary: dict[str, str] = {}
        self.binary_errors: dict[str, str] = {}

    def render(self, url: str) -> RenderedPage:
        """### Return a canned page or raise a canned failure."""
        self.rendered.append(url)
        if url in self._failures:
            raise self._failures[url]
        return self._pages[url]

    def read_text(self, url: str) -> RobotsFile:
        """### Return the canned robots file."""
        return self._robots

    def read_bytes(self, url: str) -> RobotsFile:
        """### Return a canned binary resource or a canned failure."""
        if url in self.binary_errors:
            return RobotsFile(error=self.binary_errors[url])
        return RobotsFile(status_code=200, text=self.binary.get(url, ""))

    def close(self) -> None:
        """### Record that the session was closed."""
        self.closed = True


class FakeBinaryFetcher:
    """### Serve canned binary responses through the same bounded contract as HTTP."""

    engine_name = "http"

    def __init__(self, session: FakeBrowserSession) -> None:
        """### Reuse the browser fixture's binary payloads and failures."""
        self._session = session

    def fetch(self, url: str) -> FetchedPage:
        """### Refuse unexpected text requests."""
        raise FetchError("unexpected text request", url=url)

    def fetch_binary(self, url: str, *, max_bytes: int | None = None) -> FetchedPage:
        """### Return a canned binary page if it fits within the requested bound."""
        if url in self._session.binary_errors:
            raise TransientFetchError(self._session.binary_errors[url], url=url)
        content = self._session.binary.get(url, "").encode("latin-1")
        if max_bytes is not None and len(content) > max_bytes:
            raise ResponseTooLargeError("response exceeds the download limit", url=url)
        return FetchedPage(url=url, status_code=200, content=content)

    def post_json(self, url: str, *, payload: str) -> FetchedPage:
        """### Refuse unexpected JSON posts."""
        raise FetchError("unexpected JSON post", url=url)

    def close(self) -> None:
        """### Release no resources."""
        return None


def rendered(url: str, html: str = OK_HTML, status_code: int | None = 200) -> RenderedPage:
    """### Build one rendered page."""
    return RenderedPage(url=url, status_code=status_code, html=html)


def build_fetcher(
    session: FakeBrowserSession,
    clock: ClockRecorder,
    *,
    max_retries: int = 2,
    backoff_seconds: float = 0.0,
    request_delay_seconds: float = 0.0,
    respect_robots_txt: bool = True,
    max_response_bytes: int = 26_214_400,
) -> BrowserFetcher:
    """### Build a browser fetcher bound to a synthetic session and clock."""
    return BrowserFetcher(
        user_agent="LexCrawl-test/0.1",
        session=session,
        binary_fetcher=FakeBinaryFetcher(session),
        max_retries=max_retries,
        backoff_seconds=backoff_seconds,
        request_delay_seconds=request_delay_seconds,
        respect_robots_txt=respect_robots_txt,
        max_response_bytes=max_response_bytes,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )


def test_browser_fetch_returns_utf8_html(clock: ClockRecorder) -> None:
    """### A rendered document is returned as UTF-8 HTML bytes."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL)})
    page = build_fetcher(session, clock).fetch(ARTICLE_URL)
    assert isinstance(page, FetchedPage)
    assert page.status_code == 200
    assert page.content_type == "text/html"
    assert "الخبر" in page.content.decode("utf-8")


def test_browser_rejects_a_rendered_document_over_the_size_limit(clock: ClockRecorder) -> None:
    """### Rendered page bytes above the configured cap are not returned to actors."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL, "x" * 20)})
    with pytest.raises(ResponseTooLargeError):
        build_fetcher(session, clock, max_response_bytes=10).fetch(ARTICLE_URL)


def test_browser_detects_bot_protection_page(clock: ClockRecorder) -> None:
    """### A Cloudflare denial is reported as a block with its reason."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL, BLOCK_HTML, 403)})
    with pytest.raises(AccessBlockedError) as error:
        build_fetcher(session, clock, max_retries=2).fetch(ARTICLE_URL)
    assert error.value.reason == "Attention Required! | Cloudflare"
    assert error.value.status_code == 403


def test_browser_retries_transient_failures(clock: ClockRecorder) -> None:
    """### A transient driver failure is retried and can still succeed."""
    session = FakeBrowserSession(
        {ARTICLE_URL: rendered(ARTICLE_URL)},
        failures={ARTICLE_URL: TransientFetchError("navigation timed out", url=ARTICLE_URL)},
    )
    with pytest.raises(TransientFetchError):
        build_fetcher(session, clock, max_retries=1, backoff_seconds=0.0).fetch(ARTICLE_URL)
    assert session.rendered == [ARTICLE_URL, ARTICLE_URL]


def test_browser_does_not_retry_client_errors(clock: ClockRecorder) -> None:
    """### A 404 is reported immediately without retrying."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL, "<html>missing</html>", 404)})
    with pytest.raises(FetchError) as error:
        build_fetcher(session, clock, max_retries=3).fetch(ARTICLE_URL)
    assert not isinstance(error.value, TransientFetchError)
    assert session.rendered == [ARTICLE_URL]


def test_browser_rejects_an_empty_document(clock: ClockRecorder) -> None:
    """### An empty render is a failure, never an empty article."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL, "   ")})
    with pytest.raises(FetchError, match="empty document"):
        build_fetcher(session, clock).fetch(ARTICLE_URL)


def test_browser_caches_repeated_urls(clock: ClockRecorder) -> None:
    """### The same URL is rendered only once per run."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL)})
    fetcher = build_fetcher(session, clock)
    fetcher.fetch(ARTICLE_URL)
    fetcher.fetch(ARTICLE_URL)
    assert session.rendered == [ARTICLE_URL]


def test_browser_enforces_pacing(clock: ClockRecorder) -> None:
    """### Consecutive navigations are separated by the configured delay."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL), OTHER_URL: rendered(OTHER_URL)})
    fetcher = build_fetcher(session, clock, request_delay_seconds=3.0)
    fetcher.fetch(ARTICLE_URL)
    assert clock.slept == []
    fetcher.fetch(OTHER_URL)
    assert clock.slept == [3.0]


def test_browser_honours_robots(clock: ClockRecorder) -> None:
    """### A disallowed URL is never rendered."""
    session = FakeBrowserSession(
        {ARTICLE_URL: rendered(ARTICLE_URL)},
        robots=RobotsFile(status_code=200, text="User-agent: *\nDisallow: /ar/news/\n"),
    )
    with pytest.raises(RobotsDisallowedError):
        build_fetcher(session, clock).fetch(ARTICLE_URL)
    assert session.rendered == []


def test_browser_can_skip_robots_checks(clock: ClockRecorder) -> None:
    """### Robots enforcement is configurable."""
    session = FakeBrowserSession(
        {ARTICLE_URL: rendered(ARTICLE_URL)},
        robots=RobotsFile(status_code=200, text="User-agent: *\nDisallow: /ar/news/\n"),
    )
    assert build_fetcher(session, clock, respect_robots_txt=False).fetch(ARTICLE_URL).status_code == 200


def test_browser_refuses_cross_thread_use(clock: ClockRecorder) -> None:
    """### The driver is single-threaded, so cross-thread use fails loudly."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL)})
    fetcher = build_fetcher(session, clock)
    fetcher.fetch(ARTICLE_URL)
    failure: list[BaseException] = []

    def fetch_elsewhere() -> None:
        """### Attempt a fetch from another thread."""
        try:
            fetcher.fetch(OTHER_URL)
        except BaseException as error:  # $ Capture the guard failure for the assertion below.$
            failure.append(error)

    thread = threading.Thread(target=fetch_elsewhere)
    thread.start()
    thread.join()
    assert failure and isinstance(failure[0], RuntimeError)
    assert "one thread" in str(failure[0])


def test_browser_does_not_close_an_injected_session(clock: ClockRecorder) -> None:
    """### An injected session is left open for its owner to close."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL)})
    build_fetcher(session, clock).close()
    assert session.closed is False


def test_browser_close_is_safe_without_a_launch() -> None:
    """### Closing a fetcher that never rendered cannot fail."""
    BrowserFetcher(user_agent="LexCrawl-test/0.1").close()


def test_browser_fetcher_can_be_used_as_a_context_manager(clock: ClockRecorder) -> None:
    """### The fetcher supports the closing() lifecycle used by the CLI."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL)})
    with build_fetcher(session, clock) as fetcher:
        assert fetcher.fetch(ARTICLE_URL).status_code == 200


def test_browser_fetches_binary_payloads(clock: ClockRecorder) -> None:
    """### Raw bytes are returned for payloads such as PDFs."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL)})
    session.binary = {ARTICLE_URL: "%PDF-1.7 binary"}
    fetcher = build_fetcher(session, clock)
    page = fetcher.fetch_binary(ARTICLE_URL)
    assert page.content.startswith(b"%PDF-1.7")
    assert fetcher.fetch_binary(ARTICLE_URL).content == page.content


def test_browser_reports_binary_transport_failures(clock: ClockRecorder) -> None:
    """### An unreachable binary resource is reported, never silently empty."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL)})
    session.binary_errors = {ARTICLE_URL: "connection reset"}
    with pytest.raises(FetchError):
        build_fetcher(session, clock).fetch_binary(ARTICLE_URL)


def test_browser_binary_fetch_obeys_the_pdf_size_limit(clock: ClockRecorder) -> None:
    """### Binary responses exceeding the caller's PDF limit fail before extraction."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL)})
    session.binary = {ARTICLE_URL: "%PDF-" + "x" * 20}

    with pytest.raises(ResponseTooLargeError):
        build_fetcher(session, clock).fetch_binary(ARTICLE_URL, max_bytes=10)


def test_browser_refuses_json_posts(clock: ClockRecorder) -> None:
    """### A rendering engine cannot serve a JSON feed."""
    session = FakeBrowserSession({ARTICLE_URL: rendered(ARTICLE_URL)})
    with pytest.raises(FetchError, match="cannot post JSON"):
        build_fetcher(session, clock).post_json(ARTICLE_URL, payload="{}")
