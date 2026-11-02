"""### Integration test: real Chromium rendering feeding the UAE listing parser."""

from __future__ import annotations

from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from lexcrawl.actors.uae_legislation import parse_listing
from lexcrawl.crawling.base import FetchError
from lexcrawl.crawling.browser import BrowserFetcher, PlaywrightBrowserSession

from ..samples import LISTING_URL, PAGE_TWO_URL, listing_html

pytestmark = pytest.mark.integration

DOCUMENT = listing_html(cards=2, next_page=PAGE_TWO_URL)


def build_document_server() -> tuple[ThreadingHTTPServer, str]:
    """### Start a local HTTP server that serves the canned document."""

    class DocumentHandler(BaseHTTPRequestHandler):
        """### Serve the canned listing document for every path."""

        def do_GET(self) -> None:  # $ Serve a redirect or the canned document.$
            """### Serve the canned document or one of the redirect fixtures."""
            destinations = {
                "/redirect": "/ar/news",
                "/chain-a": "/chain-b",
                "/chain-b": "/ar/news",
                "/loop-a": "/loop-b",
                "/loop-b": "/loop-a",
            }
            destination = destinations.get(self.path)
            if destination:
                self.send_response(302)
                self.send_header("Location", destination)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(DOCUMENT)))
            self.end_headers()
            self.wfile.write(DOCUMENT)

        def log_message(self, format: str, *args: object) -> None:  # $ Keep test output clean.$
            """### Silence the default stderr access log."""
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), DocumentHandler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


@pytest.fixture(scope="module")
def browser_ready(document_server: str) -> Iterator[None]:
    """### Skip the module when no working Chromium is available."""
    pytest.importorskip("playwright.sync_api")
    probe = PlaywrightBrowserSession(headless=True, settle_milliseconds=0, allowed_redirect_hosts=("127.0.0.1",))
    try:
        probe.render(document_server)
    except Exception as error:  # $ A missing browser binary is an environment limit, not a product defect.$
        pytest.skip(f"no usable Chromium for this environment: {error}")
    finally:
        probe.close()
    yield None


@pytest.fixture(scope="module")
def document_server() -> Iterator[str]:
    """### Serve the canned document over a local socket for this module."""
    server, base_url = build_document_server()
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()


def test_real_browser_render_feeds_the_listing_parser(browser_ready: None, document_server: str) -> None:
    """### A rendered document flows through the fetcher into the source parser."""
    with BrowserFetcher(
        user_agent="LexCrawl-test/0.1",
        respect_robots_txt=False,
        allowed_redirect_hosts=("127.0.0.1",),
    ) as fetcher:
        page = fetcher.fetch(f"{document_server}/ar/news")

    assert page.status_code == 200
    assert page.content_type == "text/html"
    assert "خبر رقم 1" in page.content.decode("utf-8")

    listing = parse_listing(page.content, LISTING_URL)
    assert [stub.url for stub in listing.stubs] == [f"{LISTING_URL}/article-1", f"{LISTING_URL}/article-2"]
    assert listing.stubs[0].title == "خبر رقم 1"
    assert listing.stubs[1].published_at is not None
    assert listing.next_page_url == PAGE_TWO_URL


def test_browser_validates_same_host_redirect_chains(browser_ready: None, document_server: str) -> None:
    """### Same-host redirect chains remain usable under navigation interception."""
    with BrowserFetcher(
        user_agent="LexCrawl-test/0.1",
        respect_robots_txt=False,
        allowed_redirect_hosts=("127.0.0.1",),
    ) as fetcher:
        page = fetcher.fetch(f"{document_server}/chain-a")
    assert page.status_code == 200
    assert page.url == f"{document_server}/ar/news"


def test_browser_bounds_redirect_loops(browser_ready: None, document_server: str) -> None:
    """### A redirect loop fails within the configured retry budget."""
    with (
        BrowserFetcher(
            user_agent="LexCrawl-test/0.1",
            respect_robots_txt=False,
            max_retries=0,
            allowed_redirect_hosts=("127.0.0.1",),
        ) as fetcher,
        pytest.raises(FetchError),
    ):
        fetcher.fetch(f"{document_server}/loop-a")


def test_real_browser_reads_plain_text_resources(browser_ready: None, document_server: str) -> None:
    """### The driver can retrieve non-HTML resources such as robots.txt."""
    session = PlaywrightBrowserSession(headless=True, settle_milliseconds=0, allowed_redirect_hosts=("127.0.0.1",))
    try:
        robots = session.read_text(f"{document_server}/robots.txt")
    finally:
        session.close()

    assert robots.status_code == 200
    assert robots.error is None


def test_real_browser_fails_on_an_unreachable_host(browser_ready: None) -> None:
    """### A refused connection surfaces as a fetch failure, never as empty content."""
    with (
        BrowserFetcher(
            user_agent="LexCrawl-test/0.1",
            respect_robots_txt=False,
            max_retries=0,
            allowed_redirect_hosts=("127.0.0.1",),
        ) as fetcher,
        pytest.raises(FetchError),
    ):
        fetcher.fetch("http://127.0.0.1:9/definitely-not-listening")
