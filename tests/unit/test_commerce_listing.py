"""### Unit tests for the Commerce listing page parsing."""

from __future__ import annotations

from lexcrawl.actors.commerce import (
    build_page_url,
    filename_from_url,
    is_report_url,
    parse_listing,
    parse_listing_date,
    resource_kind,
)
from samples import COMMERCE_HOST, COMMERCE_LISTING_PATH, COMMERCE_LISTING_URL, commerce_listing_html, commerce_row


def _host() -> str:
    """### Return the listing host used by the samples."""
    return COMMERCE_HOST


def _prefixes() -> tuple[str, ...]:
    """### Return the report path prefix used by the samples."""
    return (f"{COMMERCE_LISTING_PATH}/",)


def test_build_page_url_uses_explicit_query() -> None:
    """### Pagination uses the source's own ?q=<path>&page=N form."""
    assert build_page_url(COMMERCE_LISTING_URL, 3) == f"{COMMERCE_LISTING_URL}?q={COMMERCE_LISTING_PATH}&page=3"


def test_parse_listing_reads_rows_and_attachments() -> None:
    """### A listing table yields in-scope entries with their attachment links."""
    page_url = build_page_url(COMMERCE_LISTING_URL, 0)
    page = parse_listing(
        commerce_listing_html([commerce_row()], start=1, end=1, total=112),
        page_url=page_url,
        page_number=0,
        host=_host(),
        path_prefixes=_prefixes(),
    )
    assert page is not None
    assert page.total_reports == 112
    assert page.range_start == 1 and page.range_end == 1
    assert len(page.entries) == 1
    entry = page.entries[0]
    assert entry.title == "Sample Commerce report"
    assert entry.published_month == 1 and entry.published_year == 2025
    assert entry.published_date_raw == "January 2025"
    assert len(entry.resources) == 1
    assert entry.resources[0].kind == "pdf"


def test_parse_listing_returns_none_without_table() -> None:
    """### A page without the report table is not a usable listing."""
    page = parse_listing(
        commerce_listing_html([], with_table=False),
        page_url=build_page_url(COMMERCE_LISTING_URL, 0),
        page_number=0,
        host=_host(),
        path_prefixes=_prefixes(),
    )
    assert page is None


def test_parse_listing_ignores_out_of_scope_rows() -> None:
    """### Rows linking outside the report path are dropped."""
    row = ("January 2025", "Elsewhere", "/news/2025/01/other", [])
    page = parse_listing(
        commerce_listing_html([row]),
        page_url=build_page_url(COMMERCE_LISTING_URL, 0),
        page_number=0,
        host=_host(),
        path_prefixes=_prefixes(),
    )
    assert page is None


def test_parse_listing_date_supports_month_year() -> None:
    """### Month-year listing values parse without inventing a day."""
    assert parse_listing_date("January 2025").month == 1
    assert parse_listing_date("Jan 2025").year == 2025
    assert parse_listing_date("  ").month is None
    fallback = parse_listing_date("sometime")
    assert fallback.raw == "sometime" and fallback.month is None


def test_is_report_url_scope() -> None:
    """### In-scope host and path are required."""
    in_scope = f"https://{COMMERCE_HOST}/data-and-reports/reports/2025/01/x"
    assert is_report_url(in_scope, host=COMMERCE_HOST, path_prefixes=_prefixes())
    assert not is_report_url(
        "https://other.test/data-and-reports/reports/2025/01/x", host=COMMERCE_HOST, path_prefixes=_prefixes()
    )
    assert not is_report_url(f"https://{COMMERCE_HOST}/news/2025/01/x", host=COMMERCE_HOST, path_prefixes=_prefixes())


def test_resource_kind_and_filename() -> None:
    """### File kinds come from the extension and filenames are decoded."""
    assert resource_kind("https://x.test/a/report.PDF") == "pdf"
    assert resource_kind("https://x.test/a/data.xlsx") == "excel"
    assert resource_kind("https://x.test/a/notes.txt") == "other"
    assert filename_from_url("https://x.test/a/sample%20report.pdf") == "sample report.pdf"
