"""### Commerce actor tests: cheap preflight, Firecrawl pagination, selective detail."""

from __future__ import annotations

from collections.abc import Sequence

import httpx
import pytest

from lexcrawl.actors.commerce import (
    CommerceExtractionError,
    CommerceReportsActor,
    InsufficientReportContent,
    build_page_url,
    parse_detail,
    parse_listing,
)
from lexcrawl.crawling.base import AccessBlockedError
from lexcrawl.crawling.firecrawl import FirecrawlError, ScrapedPage
from lexcrawl.models.document import TEXT_BEGIN_MARKER, compute_content_hash, stable_identity
from lexcrawl.repositories.documents import IncompleteReport
from lexcrawl.validation.commerce import SOURCE_NAME

from ..conftest import StubFetcher
from ..samples import (
    COMMERCE_DETAIL_PREFIX,
    COMMERCE_HOST,
    COMMERCE_LISTING_URL,
    commerce_detail_html,
    commerce_listing_html,
)

PAGE0 = build_page_url(COMMERCE_LISTING_URL, 0)
PAGE1 = build_page_url(COMMERCE_LISTING_URL, 1)
SCOPE = {"host": COMMERCE_HOST, "path_prefixes": ("/data-and-reports/reports/",)}


def identity(index: int) -> str:
    """### Return the canonical identity for one sample report."""
    return stable_identity(SOURCE_NAME, None, f"{COMMERCE_DETAIL_PREFIX}2025/01/report-{index}")


def row(
    index: int,
    *,
    date: str = "January 2025",
    pdf: bool = False,
) -> tuple[str, str, str, list[tuple[str, str, str]]]:
    """### Build one listing row tuple, optionally with a PDF attachment."""
    detail = f"/data-and-reports/reports/2025/01/report-{index}"
    files = (
        [(f"/sites/default/files/2025-01/report-{index}.pdf", f"report-{index}.pdf", "application/pdf")] if pdf else []
    )
    return date, f"Report {index}", detail, files


def listing_page(
    indexes: Sequence[int],
    *,
    start: int,
    end: int,
    total: int = 112,
    pdf: bool = False,
) -> str:
    """### Build a listing page for the given report indexes."""
    return commerce_listing_html([row(index, pdf=pdf) for index in indexes], start=start, end=end, total=total)


def page_zero(*indexes: int, pdf: bool = False) -> str:
    """### Build the first listing page with a 20-row source range."""
    return listing_page(indexes or tuple(range(1, 21)), start=1, end=20, pdf=pdf)


class FakeScraper:
    """### Serve canned Firecrawl pages and record every call."""

    def __init__(
        self,
        pages: dict[str, str | Exception],
        *,
        target_status: int | None = 200,
        credits: int = 1,
    ) -> None:
        """### Store canned pages and the provider status to report."""
        self._pages = pages
        self._target_status = target_status
        self._credits = credits
        self.calls: list[tuple[str, bool]] = []

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Return a canned page or raise a canned failure."""
        self.calls.append((url, cache))
        value = self._pages.get(url)
        if value is None:
            raise FirecrawlError("no canned page", url=url)
        if isinstance(value, Exception):
            raise value
        return ScrapedPage(
            requested_url=url,
            provider_status=200,
            target_status=self._target_status,
            html=value,
            markdown="",
            credits_used=self._credits,
        )

    def urls(self) -> list[str]:
        """### Return the scraped URLs in order."""
        return [url for url, _ in self.calls]


def build_actor(
    scraper: FakeScraper,
    *,
    known: set[str] | None = None,
    incomplete: list[IncompleteReport] | None = None,
    bootstrap: bool = False,
    max_pages: int = 6,
    max_details: int = 20,
    max_incomplete_retries: int = 5,
    min_words: int = 5,
) -> CommerceReportsActor:
    """### Build the actor with a fake scraper and identity source."""
    return CommerceReportsActor(
        listing_url=COMMERCE_LISTING_URL,
        firecrawl=scraper,
        known_identities=(lambda: set(known or ())) if known is not None else None,
        incomplete_reports=(lambda: list(incomplete or ())) if incomplete is not None else None,
        bootstrap=bootstrap,
        max_pages=max_pages,
        max_detail_scrapes=max_details,
        max_incomplete_retries=max_incomplete_retries,
        min_body_words=min_words,
    )


def incomplete(
    index: int,
    *,
    attempts: int = 1,
    title: str | None = None,
) -> IncompleteReport:
    """### Build one persisted incomplete-report retry candidate."""
    return IncompleteReport(
        identity=identity(index),
        url=f"{COMMERCE_DETAIL_PREFIX}2025/01/report-{index}",
        title=title or f"Report {index}",
        published_date_raw="January 2025",
        published_month=1,
        published_year=2025,
        attempts=attempts,
    )


def fetcher(pages: dict[str, str], *, failures: dict[str, Exception] | None = None) -> StubFetcher:
    """### Serve canned listing pages to the preflight."""
    return StubFetcher({url: html.encode() for url, html in pages.items()}, failures=failures or {})


# $ Listing parser$


def test_parse_listing_extracts_rows_and_range() -> None:
    """### Rows, attachments, and the source range are parsed from the table."""
    page = parse_listing(page_zero(1, 2, pdf=True), page_url=PAGE0, page_number=0, **SCOPE)
    assert page is not None
    assert [entry.title for entry in page.entries] == ["Report 1", "Report 2"]
    assert page.entries[0].url == f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1"
    assert page.entries[0].published_month == 1 and page.entries[0].published_year == 2025
    assert page.entries[0].resources[0].original_url.endswith("report-1.pdf")
    assert (page.range_start, page.range_end, page.total_reports) == (1, 20, 112)


def test_parse_listing_deduplicates_and_rejects_unrelated_links() -> None:
    """### Repeated rows collapse and off-scope links are ignored."""
    html = commerce_listing_html(
        [row(1), row(1), ("January 2025", "External", "https://example.com/reports/x", [])],
        start=1,
        end=20,
    )
    page = parse_listing(html, page_url=PAGE0, page_number=0, **SCOPE)
    assert page is not None
    assert len(page.entries) == 1


def test_parse_listing_without_the_table_is_an_extraction_failure() -> None:
    """### A page without the table is a failure, never an empty page."""
    html = commerce_listing_html([], with_table=False)
    assert parse_listing(html, page_url=PAGE0, page_number=0, **SCOPE) is None


def test_parse_detail_extracts_the_report() -> None:
    """### Detail pages yield title, day-precision date, marked body, and attachments."""
    html = commerce_detail_html(
        "Sample Commerce report",
        attachments=[("/sites/default/files/2025-01/a.pdf", "a.pdf", "application/pdf")],
    )
    report = parse_detail(html, url=f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1", min_body_words=5)

    assert report.title == "Sample Commerce report"
    assert str(report.published_at) == "2025-01-17"
    assert report.published_precision == "day"
    assert report.text is not None and report.text.startswith(TEXT_BEGIN_MARKER)
    assert report.content_hash
    assert [resource.original_url for resource in report.resources] == [
        "https://www.commerce.gov/sites/default/files/2025-01/a.pdf"
    ]


def test_parse_detail_flags_heading_only_content() -> None:
    """### A heading-only page raises but carries its metadata forward."""
    html = commerce_detail_html("Heading only", paragraphs=("Heading only",))
    with pytest.raises(InsufficientReportContent) as error:
        parse_detail(html, url=f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1", min_body_words=30)
    assert error.value.words == 2
    assert error.value.report.title == "Heading only"
    assert error.value.report.text is None


def test_parse_detail_without_an_article_container_raises() -> None:
    """### A page without the article container cannot be used."""
    html = commerce_detail_html("No article", with_article=False)
    with pytest.raises(CommerceExtractionError):
        parse_detail(html, url=f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1", min_body_words=5)


# $ Preflight$


def test_no_new_reports_makes_zero_firecrawl_calls() -> None:
    """### A successful no-change preflight never touches Firecrawl."""
    scraper = FakeScraper({})
    actor = build_actor(scraper, known={identity(i) for i in range(1, 21)})
    result = actor.run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert scraper.calls == []
    assert result.check_outcome == "no_change"
    assert result.status == "success"
    assert result.documents == []
    assert result.pagination_complete is True


def test_new_report_triggers_targeted_firecrawl_acquisition() -> None:
    """### Only the new report and the next page are scraped."""
    detail_url = f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1"
    scraper = FakeScraper(
        {
            PAGE1: listing_page(range(21, 41), start=21, end=40, total=112),
            detail_url: commerce_detail_html("Report 1", paragraphs=("Body " * 40,)),
        }
    )
    known = {identity(i) for i in range(1, 41)} - {identity(1)}
    result = build_actor(scraper, known=known).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert result.check_outcome == "new_data"
    assert result.status == "success"
    assert scraper.urls() == [PAGE1, detail_url]
    assert len(result.documents) == 1
    assert result.documents[0].source_url == detail_url
    assert result.provider_credits_used == 2


def test_preflight_blocked_is_check_failed_without_firecrawl() -> None:
    """### A denied preflight is a failed check, not no-change and not a crawl."""
    scraper = FakeScraper({})
    blocked = AccessBlockedError("blocked", url=PAGE0, status_code=403, reason="cloudflare")
    result = build_actor(scraper, known=set()).run(fetcher({}, failures={PAGE0: blocked}))

    assert result.check_outcome == "check_failed"
    assert result.status == "failed"
    assert scraper.calls == []
    assert any("change detection fetch failed" in error for error in result.errors)


def test_preflight_without_a_table_is_check_failed() -> None:
    """### HTTP 200 without the listing table is a failed check."""
    scraper = FakeScraper({})
    html = commerce_listing_html([], with_table=False)
    result = build_actor(scraper, known=set()).run(fetcher({PAGE0: html}))

    assert result.check_outcome == "check_failed"
    assert scraper.calls == []


def test_preflight_without_a_baseline_is_check_failed() -> None:
    """### Change detection requires persisted identities."""
    scraper = FakeScraper({})
    result = build_actor(scraper, known=None).run(fetcher({PAGE0: page_zero()}))

    assert result.check_outcome == "check_failed"
    assert scraper.calls == []
    assert any("requires" in error for error in result.errors)


# $ Pagination$


def test_pagination_stops_at_the_overlap_boundary() -> None:
    """### Pagination ends on the first page with no new identities."""
    scraper = FakeScraper(
        {
            PAGE1: listing_page(range(21, 41), start=21, end=40, total=112),
        }
    )
    known = {identity(i) for i in range(2, 41)}
    result = build_actor(scraper, known=known, max_details=0).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert result.check_outcome == "new_data"
    assert scraper.urls() == [PAGE1]
    assert result.pagination_complete is True
    assert result.listing_pages_attempted == 1


def test_new_reports_spanning_pages_are_collected() -> None:
    """### Discovery continues while pages still contain new reports."""
    scraper = FakeScraper(
        {
            PAGE1: listing_page(range(21, 41), start=21, end=40, total=112),
            build_page_url(COMMERCE_LISTING_URL, 2): listing_page(range(41, 61), start=41, end=60, total=112),
        }
    )
    known = {identity(i) for i in range(1, 61)} - {identity(1), identity(21)}
    result = build_actor(scraper, known=known, max_details=0).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert result.check_outcome == "new_data"
    assert result.articles_discovered == 2
    assert result.documents_pending == 2
    assert scraper.urls() == [PAGE1, build_page_url(COMMERCE_LISTING_URL, 2)]
    assert result.pagination_complete is True


def test_missing_table_on_a_firecrawl_page_stops_pagination() -> None:
    """### A table-less page is an extraction failure, not the end of data."""
    scraper = FakeScraper({PAGE1: commerce_listing_html([], with_table=False)})
    known = {identity(i) for i in range(1, 21)} - {identity(1)}
    result = build_actor(scraper, known=known).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert result.pagination_complete is False
    assert result.listing_pages_failed == 1
    assert any("did not contain the report table" in error for error in result.errors)


def test_target_403_on_a_listing_page_is_not_end_of_pagination() -> None:
    """### A denied listing page stops the crawl as a failure."""
    scraper = FakeScraper({PAGE1: ""}, target_status=403)
    known = {identity(i) for i in range(1, 21)} - {identity(1)}
    result = build_actor(scraper, known=known).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert result.pagination_complete is False
    assert any("returned HTTP 403" in error for error in result.errors)


def test_range_gap_is_detected() -> None:
    """### A gap between page ranges stops the crawl."""
    scraper = FakeScraper({PAGE1: listing_page(range(30, 50), start=30, end=49, total=112)})
    known = {identity(i) for i in range(1, 21)} - {identity(1)}
    result = build_actor(scraper, known=known).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert result.pagination_complete is False
    assert any("range gap" in error for error in result.errors)


def test_changed_total_is_detected() -> None:
    """### A different reported total between pages stops the crawl."""
    scraper = FakeScraper({PAGE1: listing_page(range(21, 41), start=21, end=40, total=500)})
    known = {identity(i) for i in range(1, 21)} - {identity(1)}
    result = build_actor(scraper, known=known).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert result.pagination_complete is False
    assert any("total changed" in error for error in result.errors)


def test_range_row_mismatch_is_detected() -> None:
    """### A range that does not match the row count stops the crawl."""
    scraper = FakeScraper({PAGE1: listing_page(range(21, 41), start=21, end=99, total=112)})
    known = {identity(i) for i in range(1, 21)} - {identity(1)}
    result = build_actor(scraper, known=known).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert result.pagination_complete is False
    assert any("does not match" in error for error in result.errors)


def test_max_page_budget_is_enforced() -> None:
    """### The page budget bounds Firecrawl listing calls."""
    scraper = FakeScraper(
        {
            PAGE1: listing_page(range(21, 41), start=21, end=40, total=112),
            PAGE0.replace("page=0", "page=2"): listing_page(range(41, 61), start=41, end=60, total=112),
        }
    )
    known = {identity(i) for i in range(1, 21)} - {identity(1)}
    result = build_actor(scraper, known=known, max_pages=2, max_details=0).run(
        fetcher({PAGE0: page_zero(*range(1, 21))})
    )

    assert result.pagination_complete is False
    assert result.listing_pages_attempted == 1
    assert any("firecrawl_max_pages_per_run" in error for error in result.errors)


# $ Detail acquisition$


def test_only_new_reports_receive_detail_scrapes() -> None:
    """### Known reports never trigger a detail request."""
    scraper = FakeScraper(
        {
            PAGE1: listing_page(range(21, 41), start=21, end=40, total=112),
            f"{COMMERCE_DETAIL_PREFIX}2025/01/report-2": commerce_detail_html("Report 2", paragraphs=("Body " * 40,)),
        }
    )
    known = {identity(i) for i in range(1, 41)} - {identity(2)}
    result = build_actor(scraper, known=known).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    detail_calls = [url for url in scraper.urls() if url.startswith(COMMERCE_DETAIL_PREFIX)]
    assert detail_calls == [f"{COMMERCE_DETAIL_PREFIX}2025/01/report-2"]
    assert len(result.documents) == 1


def test_heading_only_detail_is_stored_as_an_incomplete_record() -> None:
    """### A heading-only page keeps metadata and attachments but no body."""
    detail_url = f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1"
    scraper = FakeScraper(
        {
            PAGE1: listing_page(range(21, 41), start=21, end=40, total=112),
            detail_url: commerce_detail_html("Report 1", paragraphs=("Heading only",)),
        }
    )
    known = {identity(i) for i in range(1, 41)} - {identity(1)}
    result = build_actor(scraper, known=known, min_words=30).run(fetcher({PAGE0: page_zero(*range(1, 21), pdf=True)}))

    document = result.documents[0]
    assert document.text is None
    assert document.title == "Report 1"
    assert str(document.published_at) == "2025-01-17"
    assert result.records_without_text == 1
    assert result.records_missing_required_fields == 1
    assert document.resources and document.resources[0].url.endswith("report-1.pdf")


def test_failed_detail_scrape_preserves_discovery_metadata() -> None:
    """### A provider failure still yields a metadata-only record."""
    detail_url = f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1"
    scraper = FakeScraper(
        {
            PAGE1: listing_page(range(21, 41), start=21, end=40, total=112),
            detail_url: FirecrawlError("boom", url=detail_url),
        }
    )
    known = {identity(i) for i in range(1, 41)} - {identity(1)}
    result = build_actor(scraper, known=known).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert len(result.documents) == 1
    assert result.documents[0].text is None
    assert result.documents[0].source_url == detail_url
    assert any("firecrawl detail failed" in error for error in result.errors)


def test_detail_budget_leaves_pending_work() -> None:
    """### The detail budget is enforced and remaining work is recorded."""
    scraper = FakeScraper(
        {
            PAGE1: listing_page(range(21, 41), start=21, end=40, total=112),
            f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1": commerce_detail_html("Report 1", paragraphs=("Body " * 40,)),
            f"{COMMERCE_DETAIL_PREFIX}2025/01/report-2": commerce_detail_html("Report 2", paragraphs=("Body " * 40,)),
        }
    )
    known = {identity(i) for i in range(1, 41)} - {identity(1), identity(2)}
    result = build_actor(scraper, known=known, max_details=1).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert len(result.documents) == 1
    assert result.documents_pending == 1
    assert any("detail scrape budget reached" in error for error in result.errors)


def test_multiple_attachments_are_retained_and_deduplicated() -> None:
    """### Every attachment stays with its report, repeated links collapse."""
    detail_url = f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1"
    html = commerce_detail_html(
        "Report 1",
        paragraphs=("Body " * 40,),
        attachments=[
            ("/sites/default/files/2025-01/a.pdf", "a.pdf", "application/pdf"),
            ("/sites/default/files/2025-01/b.pdf", "b.pdf", "application/pdf"),
            ("/sites/default/files/2025-01/a.pdf", "a.pdf", "application/pdf"),
        ],
    )
    scraper = FakeScraper({PAGE1: listing_page([], start=21, end=20, total=112), detail_url: html})
    known = {identity(i) for i in range(1, 41)} - {identity(1)}
    result = build_actor(scraper, known=known).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    urls = {resource.url for resource in result.documents[0].resources}
    assert len(urls) == 2
    assert any(url and url.endswith("a.pdf") for url in urls)
    assert (
        len([resource for resource in result.documents[0].resources if resource.url and resource.url.endswith("a.pdf")])
        == 1
    )


def test_blocked_pdf_does_not_invalidate_the_report() -> None:
    """### A denied attachment is recorded without losing the report body."""
    detail_url = f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1"
    pdf = "https://www.commerce.gov/sites/default/files/2025-01/report-1.pdf"
    scraper = FakeScraper(
        {
            PAGE1: listing_page([], start=21, end=20, total=112),
            detail_url: commerce_detail_html("Report 1", paragraphs=("Body " * 40,)),
        }
    )
    known = {identity(i) for i in range(1, 41)} - {identity(1)}
    blocked = AccessBlockedError("blocked", url=pdf, status_code=403, reason="cloudflare")
    result = build_actor(scraper, known=known).run(
        fetcher({PAGE0: page_zero(*range(1, 21), pdf=True)}, failures={pdf: blocked})
    )

    document = result.documents[0]
    assert document.text is not None and document.text.startswith(TEXT_BEGIN_MARKER)
    assert document.resources[0].status == "access_blocked"
    assert result.resources_failed == 1


def test_report_without_attachments_is_valid() -> None:
    """### A report with no File(s) still produces a document."""
    detail_url = f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1"
    listing = commerce_listing_html(
        [("January 2025", "Report 1", "/data-and-reports/reports/2025/01/report-1", [])], start=1, end=1
    )
    scraper = FakeScraper(
        {
            PAGE1: listing_page([], start=21, end=20, total=112),
            detail_url: commerce_detail_html("Report 1", paragraphs=("Body " * 40,)),
        }
    )
    known = {identity(i) for i in range(2, 41)}
    result = build_actor(scraper, known=known).run(fetcher({PAGE0: listing}))

    assert len(result.documents) == 1
    assert result.documents[0].resources == ()


# $ Bootstrap$


def test_bootstrap_skips_the_preflight_and_uses_firecrawl() -> None:
    """### Bootstrap acquisition never depends on a successful preflight."""
    detail_url = f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1"
    scraper = FakeScraper(
        {
            PAGE0: listing_page([1], start=1, end=1, total=1),
            detail_url: commerce_detail_html("Report 1", paragraphs=("Body " * 40,)),
        }
    )
    result = build_actor(scraper, known=set(), bootstrap=True).run(fetcher({}))

    assert result.check_outcome == "new_data"
    assert scraper.urls() == [PAGE0, detail_url]
    assert len(result.documents) == 1
    assert result.status == "success"


def test_bootstrap_without_a_firecrawl_client_fails_clearly() -> None:
    """### The actor refuses to run without an acquisition client."""
    actor = CommerceReportsActor(
        listing_url=COMMERCE_LISTING_URL,
        firecrawl=None,
        known_identities=lambda: set(),
        bootstrap=True,
    )
    result = actor.run(fetcher({}))
    assert result.check_outcome == "check_failed"
    assert any("firecrawl is not configured" in error for error in result.errors)


# $ Discovery consistency$


def test_repeated_listing_page_is_detected_as_overlap() -> None:
    """### A page repeating an earlier page is an inconsistency, not an overlap boundary."""
    scraper = FakeScraper({PAGE1: listing_page(range(1, 21), start=1, end=20, total=112)})
    known = {identity(i) for i in range(2, 41)}
    result = build_actor(scraper, known=known, max_details=0).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert result.pagination_complete is False
    assert any("repeated" in error and "earlier page" in error for error in result.errors)
    assert result.articles_discovered == 1


def test_expected_reports_missing_from_an_inconsistent_page_are_reported() -> None:
    """### Expected new identities are flagged when page 0 cannot confirm them."""
    html = commerce_listing_html([row(1), row(2)], start=1, end=20, total=112)
    scraper = FakeScraper({})
    result = build_actor(scraper, known=set(), max_details=0).run(fetcher({PAGE0: html}))

    assert result.check_outcome == "new_data"
    assert result.pagination_complete is False
    assert result.articles_discovered == 0
    assert any("were not found while paginating" in error for error in result.errors)
    assert scraper.calls == []


def test_articles_discovered_is_recorded_when_pagination_aborts() -> None:
    """### The discovered count survives an early listing extraction failure."""
    scraper = FakeScraper({PAGE1: commerce_listing_html([], with_table=False)})
    known = {identity(i) for i in range(4, 41)}
    result = build_actor(scraper, known=known).run(fetcher({PAGE0: page_zero(*range(1, 21))}))

    assert result.pagination_complete is False
    assert result.articles_discovered == 3


# $ Incomplete-report retry$


def test_no_change_run_retries_only_incomplete_reports() -> None:
    """### A no-change run retries stored incomplete reports without listing credits."""
    detail_url = f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1"
    scraper = FakeScraper({detail_url: commerce_detail_html("Report 1", paragraphs=("Body " * 40,))})
    known = {identity(i) for i in range(1, 21)}
    result = build_actor(scraper, known=known, incomplete=[incomplete(1)]).run(
        fetcher({PAGE0: page_zero(*range(1, 21))})
    )

    assert result.check_outcome == "no_change"
    assert result.status == "success"
    assert scraper.urls() == [detail_url]
    assert len(result.documents) == 1
    assert result.documents[0].text is not None


def test_incomplete_retries_are_bounded_per_run() -> None:
    """### The per-run retry cap limits how many incomplete reports are re-scraped."""
    first = incomplete(1)
    second = incomplete(2)
    scraper = FakeScraper(
        {
            first.url: commerce_detail_html("Report 1", paragraphs=("Body " * 40,)),
            second.url: commerce_detail_html("Report 2", paragraphs=("Body " * 40,)),
        }
    )
    known = {identity(i) for i in range(1, 21)}
    result = build_actor(scraper, known=known, incomplete=[first, second], max_incomplete_retries=1).run(
        fetcher({PAGE0: page_zero(*range(1, 21))})
    )

    assert scraper.urls() == [first.url]
    assert len(result.documents) == 1


def test_failed_check_defers_incomplete_retries_without_firecrawl() -> None:
    """### A blocked preflight never launches Firecrawl, even for pending retries."""
    scraper = FakeScraper({})
    blocked = AccessBlockedError("blocked", url=PAGE0, status_code=403, reason="cloudflare")
    result = build_actor(scraper, known=set(), incomplete=[incomplete(1)]).run(fetcher({}, failures={PAGE0: blocked}))

    assert result.check_outcome == "check_failed"
    assert scraper.calls == []
    assert result.documents_pending == 1


def test_failed_incomplete_retry_preserves_stored_metadata() -> None:
    """### A retry that fails again keeps the stored title and report identity."""
    detail_url = f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1"
    scraper = FakeScraper({detail_url: FirecrawlError("still blocked", url=detail_url)})
    known = {identity(i) for i in range(1, 21)}
    result = build_actor(scraper, known=known, incomplete=[incomplete(1, title="Stored title")]).run(
        fetcher({PAGE0: page_zero(*range(1, 21))})
    )

    assert len(result.documents) == 1
    assert result.documents[0].text is None
    assert result.documents[0].source_url == detail_url
    assert result.documents[0].title == "Stored title"


# $ PDF reuse$


def build_pdf(text: str) -> bytes:
    """### Build a real in-memory PDF for the resource-extraction test."""
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


def test_discovered_pdf_runs_through_extraction_with_marker() -> None:
    """### A discovered PDF reuses the existing extraction flow and keeps TEXT-BEGIN."""
    detail_url = f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1"
    pdf = "https://www.commerce.gov/sites/default/files/2025-01/report-1.pdf"
    scraper = FakeScraper(
        {
            PAGE1: listing_page(range(21, 41), start=21, end=40, total=112),
            detail_url: commerce_detail_html("Report 1", paragraphs=("Body " * 40,)),
        }
    )
    known = {identity(i) for i in range(1, 41)} - {identity(1)}
    source = StubFetcher(
        {PAGE0: page_zero(*range(1, 21), pdf=True).encode("utf-8"), pdf: build_pdf("Attachment body text")},
        content_types={pdf: "application/pdf"},
    )
    result = build_actor(scraper, known=known).run(source)

    assert result.status == "success"
    document = result.documents[0]
    assert document.text is not None and document.text.startswith(TEXT_BEGIN_MARKER)
    resource = document.resources[0]
    assert resource.status == "complete"
    assert resource.text is not None and resource.text.startswith(TEXT_BEGIN_MARKER)
    assert resource.content_hash == compute_content_hash(resource.text)
    assert result.resources_extracted == 1


# $ Attachment failure isolation$


def malformed_attachment_detail() -> tuple[str, str, str]:
    """### Build the detail URL and the two attachment URLs shared by the isolation tests."""
    detail_url = f"{COMMERCE_DETAIL_PREFIX}2025/01/report-1"
    first = "https://www.commerce.gov/sites/default/files/2025-01/a.pdf"
    second = "https://www.commerce.gov/sites/default/files/2025-01/b.pdf"
    return detail_url, first, second


def test_malformed_attachment_does_not_abort_the_run() -> None:
    """### A decompression failure on one attachment is isolated and the report survives."""
    detail_url, bad, good = malformed_attachment_detail()
    scraper = FakeScraper(
        {
            PAGE1: listing_page([], start=21, end=20, total=112),
            detail_url: commerce_detail_html(
                "Report 1",
                paragraphs=("Body " * 40,),
                attachments=[
                    ("/sites/default/files/2025-01/a.pdf", "a.pdf", "application/pdf"),
                    ("/sites/default/files/2025-01/b.pdf", "b.pdf", "application/pdf"),
                ],
            ),
        }
    )
    known = {identity(i) for i in range(1, 41)} - {identity(1)}
    source = StubFetcher(
        {PAGE0: page_zero(*range(1, 21)).encode("utf-8"), good: build_pdf("Attachment B text")},
        failures={bad: httpx.DecodingError("Error -3 while decompressing data")},
        content_types={good: "application/pdf"},
    )
    result = build_actor(scraper, known=known).run(source)

    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.text is not None and document.text.startswith(TEXT_BEGIN_MARKER)
    resources = {resource.url: resource for resource in document.resources}
    assert resources[bad].status == "failed"
    assert "attachment fetch failed" in (resources[bad].error or "")
    assert resources[good].status == "complete"
    assert (resources[good].text or "").startswith(TEXT_BEGIN_MARKER)
    assert result.resources_failed == 1
    assert result.resources_extracted == 1
    assert any("a.pdf" in error for error in result.errors)


def test_every_malformed_attachment_leaves_the_report_intact() -> None:
    """### Attachments that all fail to decode still yield the parent report."""
    detail_url, first, second = malformed_attachment_detail()
    scraper = FakeScraper(
        {
            PAGE1: listing_page([], start=21, end=20, total=112),
            detail_url: commerce_detail_html(
                "Report 1",
                paragraphs=("Body " * 40,),
                attachments=[
                    ("/sites/default/files/2025-01/a.pdf", "a.pdf", "application/pdf"),
                    ("/sites/default/files/2025-01/b.pdf", "b.pdf", "application/pdf"),
                ],
            ),
        }
    )
    known = {identity(i) for i in range(1, 41)} - {identity(1)}
    source = StubFetcher(
        {PAGE0: page_zero(*range(1, 21)).encode("utf-8")},
        failures={
            first: httpx.DecodingError("Error -3 while decompressing data"),
            second: httpx.DecodingError("invalid code lengths set"),
        },
    )
    result = build_actor(scraper, known=known).run(source)

    assert len(result.documents) == 1
    assert result.documents[0].text is not None
    assert {resource.status for resource in result.documents[0].resources} == {"failed"}
    assert result.resources_extracted == 0
    assert result.resources_failed == 2
