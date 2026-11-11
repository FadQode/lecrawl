"""### End-to-end actor tests for the Commerce source through a fake Firecrawl provider."""

from __future__ import annotations

from lexcrawl.actors.commerce import CommerceReportsActor, build_page_url
from lexcrawl.crawling.firecrawl import FirecrawlError, ScrapedPage
from lexcrawl.models.document import TEXT_BEGIN_MARKER
from samples import COMMERCE_LISTING_URL, commerce_detail_html, commerce_listing_html, commerce_row

DETAIL_A = "https://www.commerce.test/data-and-reports/reports/2025/01/report-a"
DETAIL_B = "https://www.commerce.test/data-and-reports/reports/2025/02/report-b"


def _listing_url(page: int) -> str:
    """### Return the explicit listing page URL Firecrawl is asked for."""
    return build_page_url(COMMERCE_LISTING_URL, page)


def _scraped(url: str, html: str, *, credits: int = 1, status: int | None = 200) -> ScrapedPage:
    """### Build a successful scrape result."""
    return ScrapedPage(
        requested_url=url,
        provider_status=200,
        target_status=status,
        html=html,
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
    max_pages: int = 2,
    max_details: int = 10,
    min_words: int = 5,
) -> CommerceReportsActor:
    """### Build a Commerce actor pointed at the fake provider."""
    return CommerceReportsActor(
        listing_url=COMMERCE_LISTING_URL,
        firecrawl=firecrawl,
        max_pages=max_pages,
        max_detail_scrapes=max_details,
        min_body_words=min_words,
    )


def test_actor_acquires_reports() -> None:
    """### A listing page and its details produce complete canonical documents."""
    listing = commerce_listing_html(
        [commerce_row(slug="report-a", month="01"), commerce_row(slug="report-b", month="02")],
        start=1,
        end=2,
        total=2,
    )
    firecrawl = FakeFirecrawl(
        {
            _listing_url(0): _scraped(_listing_url(0), listing),
            DETAIL_A: _scraped(DETAIL_A, commerce_detail_html(title="Report A"), credits=1),
            DETAIL_B: _scraped(DETAIL_B, commerce_detail_html(title="Report B"), credits=1),
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
    assert result.provider_credits_used == 3


def test_actor_detects_repeated_listing_page() -> None:
    """### A page repeating earlier identities marks pagination incomplete."""
    page_rows = [commerce_row(slug="report-a"), commerce_row(slug="report-b")]
    first = commerce_listing_html(page_rows, start=1, end=2, total=4)
    repeated = commerce_listing_html(page_rows, start=1, end=2, total=4)
    firecrawl = FakeFirecrawl(
        {
            _listing_url(0): _scraped(_listing_url(0), first),
            _listing_url(1): _scraped(_listing_url(1), repeated),
        }
    )
    result = _actor(firecrawl).run(None)

    assert result.pagination_complete is False
    assert any("repeated" in error for error in result.errors)


def test_actor_stops_at_detail_budget() -> None:
    """### Reports beyond the detail budget are reported as pending, not lost."""
    listing = commerce_listing_html(
        [commerce_row(slug="report-a", month="01"), commerce_row(slug="report-b", month="02")],
        start=1,
        end=2,
        total=2,
    )
    firecrawl = FakeFirecrawl(
        {
            _listing_url(0): _scraped(_listing_url(0), listing),
            DETAIL_A: _scraped(DETAIL_A, commerce_detail_html(title="Report A")),
        }
    )
    result = _actor(firecrawl, max_details=1).run(None)

    assert result.documents_pending == 1
    assert len(result.documents) == 1
    assert any("pending" in error for error in result.errors)


def test_actor_reports_firecrawl_failure() -> None:
    """### A provider failure is surfaced, never reported as a successful empty run."""
    firecrawl = FakeFirecrawl({_listing_url(0): FirecrawlError("blocked", url=_listing_url(0), retryable=False)})
    result = _actor(firecrawl).run(None)

    assert result.status == "failed"
    assert not result.documents
    assert any("firecrawl listing failed" in error for error in result.errors)


def test_actor_keeps_metadata_on_thin_detail() -> None:
    """### A thin detail page keeps discovery metadata and is marked failed."""
    listing = commerce_listing_html([commerce_row(slug="report-a", month="01")], start=1, end=1, total=1)
    thin = commerce_detail_html(title="Report A", paragraphs=("Too short.",), published=None)
    firecrawl = FakeFirecrawl(
        {
            _listing_url(0): _scraped(_listing_url(0), listing),
            DETAIL_A: _scraped(DETAIL_A, thin),
        }
    )
    result = _actor(firecrawl, min_words=30).run(None)

    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.extraction_status == "failed"
    assert document.text is None
    assert document.published_precision == "month"
    assert result.records_without_text == 1
