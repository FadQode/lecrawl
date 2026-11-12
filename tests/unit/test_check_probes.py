"""### Unit tests for the per-source change probes."""

from __future__ import annotations

from dataclasses import dataclass

from lexcrawl.checks.base import evaluate
from lexcrawl.checks.cba import CbaCheck
from lexcrawl.checks.commerce import CommerceCheck
from lexcrawl.checks.uae import UaeCheck
from lexcrawl.crawling.base import FetchedPage
from lexcrawl.crawling.scrape import ScrapedPage, ScrapeError
from samples import COMMERCE_LISTING_URL, UAE_LISTING_URL, commerce_listing_html, commerce_row, uae_listing_html

Cba_BASE = "https://cba.example.test"


class FakeScraper:
    """### Returns a canned page or raises a canned error."""

    def __init__(self, *, page: ScrapedPage | None = None, error: Exception | None = None) -> None:
        """### Store the canned outcome."""
        self._page = page
        self._error = error

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Return the canned page or raise the canned error."""
        if self._error is not None:
            raise self._error
        assert self._page is not None
        return self._page

    def close(self) -> None:
        """### No-op close."""
        return None


@dataclass
class FakeFetcher:
    """### Returns canned bytes for any URL."""

    content: bytes

    def fetch(self, url: str) -> FetchedPage:
        """### Return the canned page."""
        return FetchedPage(url=url, status_code=200, content=self.content, content_type="application/xml")

    def close(self) -> None:
        """### No-op close."""
        return None


def _uae_scraper(*, cards: int) -> FakeScraper:
    """### Build a scraper returning a UAE listing."""
    html = uae_listing_html(cards=cards, next_page=None).decode()
    page = ScrapedPage(
        requested_url=UAE_LISTING_URL, provider_status=200, target_status=200, html=html, markdown="", credits_used=0
    )
    return FakeScraper(page=page)


def test_uae_probe_fingerprints_the_listing() -> None:
    """### The UAE probe returns a stable fingerprint for the same listing."""
    first = UaeCheck(listing_url=UAE_LISTING_URL, scraper=_uae_scraper(cards=3)).probe()
    second = UaeCheck(listing_url=UAE_LISTING_URL, scraper=_uae_scraper(cards=3)).probe()
    assert first.ok and second.ok
    assert first.fingerprint is not None and len(first.fingerprint) == 64
    assert first.fingerprint == second.fingerprint


def test_uae_probe_detects_change() -> None:
    """### A changed listing changes the fingerprint."""
    before = UaeCheck(listing_url=UAE_LISTING_URL, scraper=_uae_scraper(cards=3)).probe()
    after = UaeCheck(listing_url=UAE_LISTING_URL, scraper=_uae_scraper(cards=4)).probe()
    assert before.fingerprint != after.fingerprint
    assert evaluate(after, before.fingerprint) == "new_data"


def test_uae_probe_fails_closed() -> None:
    """### A fetch failure yields a fail-closed verdict, not a change."""
    check = UaeCheck(
        listing_url=UAE_LISTING_URL,
        scraper=FakeScraper(error=ScrapeError("challenge", url=UAE_LISTING_URL, status_code=403)),
    )
    probe = check.probe()
    assert not probe.ok
    assert evaluate(probe, None) == "check_failed"


def test_cba_probe_fingerprints_sitemap() -> None:
    """### The CBA probe fingerprints article count and newest lastmod."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{Cba_BASE}/</loc><lastmod>2026-11-11</lastmod></url>"
        f"<url><loc>{Cba_BASE}/article/one</loc><lastmod>2026-11-06</lastmod></url>"
        f"<url><loc>{Cba_BASE}/article/two</loc><lastmod>2026-11-04</lastmod></url>"
        "</urlset>"
    ).encode()
    check = CbaCheck(sitemap_url=f"{Cba_BASE}/sitemap.xml", fetcher=FakeFetcher(content=xml))
    probe = check.probe()
    assert probe.ok
    assert probe.fingerprint == "2:2026-11-06"


def test_cba_probe_requires_articles() -> None:
    """### A sitemap without article locs fails closed."""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{Cba_BASE}/</loc><lastmod>2026-11-11</lastmod></url>"
        "</urlset>"
    ).encode()
    probe = CbaCheck(sitemap_url=f"{Cba_BASE}/sitemap.xml", fetcher=FakeFetcher(content=xml)).probe()
    assert not probe.ok
    assert evaluate(probe, None) == "check_failed"


def _commerce_scraper(*, total: int) -> FakeScraper:
    """### Build a scraper returning a Commerce listing with the given total."""
    html = commerce_listing_html(
        [commerce_row(slug="report-a", month="01"), commerce_row(slug="report-b", month="02")],
        start=1,
        end=2,
        total=total,
    )
    page = ScrapedPage(
        requested_url=COMMERCE_LISTING_URL,
        provider_status=200,
        target_status=200,
        html=html,
        markdown="",
        credits_used=1,
    )
    return FakeScraper(page=page)


def test_commerce_probe_fingerprints_head_and_total() -> None:
    """### The Commerce probe returns a stable head-plus-total fingerprint."""
    probe = CommerceCheck(listing_url=COMMERCE_LISTING_URL, scraper=_commerce_scraper(total=112)).probe()
    assert probe.ok
    assert probe.fingerprint is not None and probe.fingerprint.endswith(":112")


def test_commerce_probe_detects_total_change() -> None:
    """### A changed total changes the fingerprint and reads as new data."""
    before = CommerceCheck(listing_url=COMMERCE_LISTING_URL, scraper=_commerce_scraper(total=112)).probe()
    after = CommerceCheck(listing_url=COMMERCE_LISTING_URL, scraper=_commerce_scraper(total=113)).probe()
    assert before.fingerprint != after.fingerprint
    assert evaluate(after, before.fingerprint) == "new_data"
