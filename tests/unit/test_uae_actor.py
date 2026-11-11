"""### End-to-end actor tests for the UAE source through a fake Firecrawl provider."""

from __future__ import annotations

import httpx
import respx

from lexcrawl.actors.uae_legislation import UaeLegislationActor
from lexcrawl.crawling.direct import DirectHtmlScraper
from lexcrawl.crawling.firecrawl import FirecrawlError, ScrapedPage
from lexcrawl.models.document import TEXT_BEGIN_MARKER
from samples import UAE_HOST, UAE_LISTING_URL, UAE_PAGE_TWO_URL, uae_detail_html, uae_listing_html

DETAIL_1 = f"{UAE_LISTING_URL}/article-1"
DETAIL_2 = f"{UAE_LISTING_URL}/article-2"


def _scraped(url: str, html: str, *, credits: int = 1, status: int | None = 200) -> ScrapedPage:
    """### Build a successful scrape result."""
    return ScrapedPage(
        requested_url=url,
        provider_status=200,
        target_status=status,
        html=html.decode() if isinstance(html, bytes) else html,
        markdown="",
        credits_used=credits,
    )


class FakeFirecrawl:
    """### Returns canned scrapes and records every requested URL."""

    def __init__(self, responses: dict[str, ScrapedPage | Exception]) -> None:
        """### Store the canned responses keyed by requested URL."""
        self.responses = responses
        self.calls: list[str] = []

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Return the canned page or raise the canned error."""
        self.calls.append(url)
        response = self.responses.get(url)
        if response is None:
            raise FirecrawlError("no canned response", url=url)
        if isinstance(response, Exception):
            raise response
        return response


def _actor(
    firecrawl: FakeFirecrawl,
    *,
    max_pages: int = 5,
    max_details: int = 10,
    min_words: int = 3,
) -> UaeLegislationActor:
    """### Build a UAE actor pointed at the fake provider."""
    return UaeLegislationActor(
        listing_url=UAE_LISTING_URL,
        firecrawl=firecrawl,
        max_pages=max_pages,
        max_detail_scrapes=max_details,
        min_body_words=min_words,
    )


def test_actor_acquires_articles() -> None:
    """### A single listing page and its details produce complete canonical documents."""
    firecrawl = FakeFirecrawl(
        {
            UAE_LISTING_URL: _scraped(UAE_LISTING_URL, uae_listing_html(cards=2, next_page=None)),
            DETAIL_1: _scraped(DETAIL_1, uae_detail_html(title="الخبر الأول")),
            DETAIL_2: _scraped(DETAIL_2, uae_detail_html(title="الخبر الثاني")),
        }
    )
    result = _actor(firecrawl).run(None)

    assert result.status == "success"
    assert result.pagination_complete is True
    assert result.articles_discovered == 2
    assert len(result.documents) == 2
    assert all(
        document.text is not None and document.text.startswith(TEXT_BEGIN_MARKER)
        for document in result.documents
    )
    assert all(document.language == "ar" for document in result.documents)
    assert result.provider_credits_used == 3


def test_actor_follows_next_page_links() -> None:
    """### Pagination follows the source's own next-page control to the end."""
    firecrawl = FakeFirecrawl(
        {
            UAE_LISTING_URL: _scraped(UAE_LISTING_URL, uae_listing_html(cards=2, next_page=UAE_PAGE_TWO_URL)),
            UAE_PAGE_TWO_URL: _scraped(UAE_PAGE_TWO_URL, uae_listing_html(cards=1, next_page=None)),
            DETAIL_1: _scraped(DETAIL_1, uae_detail_html()),
            DETAIL_2: _scraped(DETAIL_2, uae_detail_html()),
        }
    )
    result = _actor(firecrawl).run(None)

    assert result.pagination_complete is True
    assert result.listing_pages_succeeded == 2
    assert result.articles_discovered == 2
    assert result.article_urls_deduplicated >= 1


def test_actor_detects_listing_loop() -> None:
    """### A next-page link pointing back to a visited page stops the crawl."""
    firecrawl = FakeFirecrawl(
        {UAE_LISTING_URL: _scraped(UAE_LISTING_URL, uae_listing_html(cards=1, next_page=UAE_LISTING_URL))}
    )
    result = _actor(firecrawl).run(None)

    assert result.pagination_complete is False
    assert any("loop" in error for error in result.errors)


def test_actor_stops_at_detail_budget() -> None:
    """### Articles beyond the detail budget are reported as pending."""
    firecrawl = FakeFirecrawl(
        {
            UAE_LISTING_URL: _scraped(UAE_LISTING_URL, uae_listing_html(cards=2, next_page=None)),
            DETAIL_1: _scraped(DETAIL_1, uae_detail_html()),
        }
    )
    result = _actor(firecrawl, max_details=1).run(None)

    assert result.documents_pending == 1
    assert len(result.documents) == 1
    assert any("pending" in error for error in result.errors)


def test_actor_reports_firecrawl_failure() -> None:
    """### A provider failure is surfaced, never reported as a successful empty run."""
    firecrawl = FakeFirecrawl({UAE_LISTING_URL: FirecrawlError("blocked", url=UAE_LISTING_URL, retryable=False)})
    result = _actor(firecrawl).run(None)

    assert result.status == "failed"
    assert not result.documents
    assert any("firecrawl listing failed" in error for error in result.errors)


def test_actor_keeps_metadata_on_thin_detail() -> None:
    """### A thin detail page keeps listing metadata and is marked failed."""
    firecrawl = FakeFirecrawl(
        {
            UAE_LISTING_URL: _scraped(UAE_LISTING_URL, uae_listing_html(cards=1, next_page=None)),
            DETAIL_1: _scraped(DETAIL_1, uae_detail_html(paragraphs=("قصير.",), date_text=None)),
        }
    )
    result = _actor(firecrawl, min_words=30).run(None)

    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.extraction_status == "failed"
    assert document.text is None
    assert document.published_at is not None  # filled from the listing card
    assert result.records_without_text == 1


@respx.mock
def test_actor_runs_over_direct_scraper() -> None:
    """### The UAE actor acquires listing and detail directly, spending no provider credits."""
    respx.get(UAE_LISTING_URL).mock(
        return_value=httpx.Response(
            200, content=uae_listing_html(cards=1, next_page=None), headers={"content-type": "text/html"}
        )
    )
    respx.get(DETAIL_1).mock(
        return_value=httpx.Response(200, content=uae_detail_html(), headers={"content-type": "text/html"})
    )
    scraper = DirectHtmlScraper(
        user_agent="LexCrawl/0.1 (+contact)",
        timeout_seconds=10,
        retry_delay_seconds=0.0,
        allowed_hosts=(UAE_HOST,),
        sleep=lambda _seconds: None,
    )
    actor = UaeLegislationActor(
        listing_url=UAE_LISTING_URL,
        firecrawl=scraper,
        max_pages=2,
        max_detail_scrapes=5,
        min_body_words=3,
    )
    result = actor.run(None)
    scraper.close()

    assert result.status == "success"
    assert len(result.documents) == 1
    assert result.documents[0].text is not None
    assert result.provider_credits_used == 0
