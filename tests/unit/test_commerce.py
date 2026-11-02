"""### Actor tests for the Commerce reports source."""

from __future__ import annotations

from typing import Any

import pymupdf
import pytest

from lexcrawl.actors.commerce import (
    CommerceApiError,
    CommerceReportsActor,
    build_page_url,
    is_news_release,
    parse_api_page,
    with_api_key,
)
from lexcrawl.crawling.base import AccessBlockedError, FetchError
from lexcrawl.models.document import TEXT_BEGIN_MARKER, CanonicalDocument
from lexcrawl.validation.commerce import from_api_payload, is_report_url

from ..conftest import StubFetcher
from ..samples import REPORTS_API_URL, REPORTS_ARTICLE_PATHS, REPORTS_HOST, commerce_page, commerce_record

FIRST_PAGE_URL = build_page_url(REPORTS_API_URL, api_key="TEST_KEY", page_size=50, offset=0)
SCOPE = {"host": REPORTS_HOST, "path_prefixes": REPORTS_ARTICLE_PATHS}


def page_url(page_size: int, offset: int) -> str:
    """### Build the API page URL the actor will request for a given size and offset."""
    return build_page_url(REPORTS_API_URL, api_key="TEST_KEY", page_size=page_size, offset=offset)


def report_url(slug: str, *, year: str = "2026", month: str = "01", kind: str = "reports") -> str:
    """### Build an in-scope report URL on the sample host."""
    return f"https://{REPORTS_HOST}/{kind}/{year}/{month}/{slug}"


def build_actor(
    *, page_size: int = 50, max_pages: int = 2, news_types: tuple[str, ...] = ("Reports",)
) -> CommerceReportsActor:
    """### Build the actor against the sample source configuration."""
    return CommerceReportsActor(
        api_url=REPORTS_API_URL,
        api_key="TEST_KEY",
        article_host=REPORTS_HOST,
        article_paths=REPORTS_ARTICLE_PATHS,
        news_types=news_types,
        page_size=page_size,
        max_pages=max_pages,
    )


def build_fetcher(
    pages: dict[str, bytes],
    *,
    failures: dict[str, Exception] | None = None,
    content_types: dict[str, str] | None = None,
) -> StubFetcher:
    """### Serve canned API payloads."""
    return StubFetcher(
        pages,
        failures=failures or {},
        content_type="application/vnd.api+json",
        content_types=content_types,
    )


def build_pdf(text: str) -> bytes:
    """### Create a small synthetic PDF for offline attachment tests."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


def test_actor_maps_a_report_into_the_contract() -> None:
    """### A report maps observed API text, identity, date and provenance into canonical output."""
    record = commerce_record()
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: commerce_page([record])}))

    assert result.status == "success"
    assert result.listing_pages_succeeded == 1
    assert result.articles_discovered == 1
    assert len(result.documents) == 1

    document = result.documents[0]
    assert document.source == "commerce_reports"
    assert document.source_url == record["href"]
    assert document.title == record["label"]
    assert document.language == "en"
    assert document.text is not None and document.text.startswith(TEXT_BEGIN_MARKER)
    assert document.content_hash
    assert document.native_id == str(record["id"])
    assert document.identity_key == f"commerce_reports:id:{record['id']}"
    assert document.published_at.isoformat() == "2026-01-15"
    assert document.source_created_at is not None
    assert document.source_created_at.year == 2026
    assert document.published_month is None and document.published_year == 2026
    assert document.published_precision == "day"
    assert document.summary == "A short teaser."
    assert document.provenance == "live"
    assert document.archive_url is None


def test_api_mapping_normalizes_nested_html_and_uses_teaser_precedence() -> None:
    """### API text is readable, scripts are omitted, and created is not publication date."""
    record = commerce_record(
        body="<div><ul><li><p>First &amp; clear.</p></li><li><p>Second point.</p></li></ul>"
        "<script>tracking()</script><style>.hidden{display:none}</style></div>",
        teaser="<p>Teaser &amp; details.</p>",
        subtitle="Subtitle fallback",
        created=1,
        post_date="2024-05-06",
    )
    report = from_api_payload(record)
    assert report.text == "TEXT-BEGIN\nFirst & clear.\n\nSecond point."
    assert "tracking" not in (report.text or "")
    assert report.summary == "Teaser & details."
    assert report.published_at.isoformat() == "2024-05-06"
    assert report.api_created_at is not None and report.api_created_at.year == 1970


def test_api_mapping_supports_month_year_and_keeps_unknown_date_raw() -> None:
    """### Publication precision is retained without assigning a missing day."""
    report = from_api_payload(commerce_record(post_date=None, post_date_formatted="January 2024"))
    assert report.published_at is None
    assert (report.published_month, report.published_year, report.published_precision) == (1, 2024, "month")
    unknown = from_api_payload(commerce_record(post_date="the other day", post_date_formatted=None))
    assert unknown.published_date_raw == "the other day"
    assert unknown.published_precision is None


def test_api_mapping_uses_subtitle_when_teaser_is_empty() -> None:
    """### Subtitle supplies the canonical summary when teaser is absent."""
    report = from_api_payload(commerce_record(teaser="  ", subtitle="<p>Secondary summary.</p>"))
    assert report.summary == "Secondary summary."
    assert from_api_payload(commerce_record(teaser=None, subtitle=None)).summary is None


def test_api_mapping_resolves_explicit_relative_attachment_urls() -> None:
    """### A real relative download URL is resolved without constructing a URL from a title."""
    record = commerce_record(documents=[{"id": "file-9", "title": "attachment.pdf", "url": "/files/attachment.pdf"}])
    report = from_api_payload(record)
    assert report.resources[0].native_id == "file-9"
    assert report.resources[0].original_url == f"https://{REPORTS_HOST}/files/attachment.pdf"


def test_actor_preserves_attachment_metadata_without_fabricating_urls() -> None:
    """### Attachments without an API URL keep their metadata and are marked unavailable."""
    record = commerce_record(
        record_id="208",
        documents=[
            {
                "id": "1854",
                "title": "report.pdf",
                "filemime": "application/pdf",
                "filesize": "1646999",
                "href": {},
            }
        ],
    )
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: commerce_page([record])}))
    document = result.documents[0]

    assert len(document.resources) == 1
    resource = document.resources[0]
    assert resource.native_id == "1854"
    assert resource.parent_identity == document.identity_key
    assert resource.url is None
    assert resource.kind == "pdf"
    assert resource.mime_type == "application/pdf"
    assert resource.file_size == 1_646_999
    assert resource.status == "url_unavailable"
    assert resource.text is None
    assert result.status == "partial_failure"
    assert (result.resources_discovered, result.resources_processed, result.resources_unavailable) == (1, 1, 1)


def test_actor_extracts_declared_pdf_urls_independently_of_report_text() -> None:
    """### Linked PDFs are extracted onto their own resources and non-PDF files are skipped."""
    first_url = f"https://{REPORTS_HOST}/files/first.pdf"
    missing_url = f"https://{REPORTS_HOST}/files/missing.pdf"
    sheet_url = f"https://{REPORTS_HOST}/files/table.xlsx"
    record = commerce_record(
        documents=[
            {"title": "first.pdf", "filemime": "application/pdf", "url": first_url},
            {"title": "missing.pdf", "filemime": "application/pdf", "url": missing_url},
            {
                "title": "table.xlsx",
                "filemime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "url": sheet_url,
            },
        ]
    )
    fetcher = build_fetcher(
        {FIRST_PAGE_URL: commerce_page([record]), first_url: build_pdf("Attachment text")},
        failures={missing_url: FetchError("not found", url=missing_url, status_code=404)},
        content_types={first_url: "application/pdf"},
    )

    result = build_actor().run(fetcher)
    document = result.documents[0]

    assert document.text is not None and "First report paragraph" in document.text
    first, missing, sheet = document.resources
    assert first.status == "complete" and first.text and "Attachment text" in first.text
    assert first.text != document.text and first.page_count == 1
    assert missing.status == "url_unavailable" and missing.text is None
    assert sheet.status == "not_applicable" and sheet.url == sheet_url
    assert sheet_url not in fetcher.calls
    assert result.status == "partial_failure"
    assert (result.resources_discovered, result.resources_processed) == (3, 3)
    assert (result.resources_extracted, result.resources_unavailable, result.resources_skipped) == (1, 1, 1)


def test_actor_maps_the_lowercase_image_field() -> None:
    """### The API image field is lowercase and reaches the canonical document."""
    record = commerce_record(
        image={"ImageElements": [{"ImageUrl": "https://www.commerce.gov/sites/default/files/img.png"}]}
    )
    document = build_actor().run(build_fetcher({FIRST_PAGE_URL: commerce_page([record])})).documents[0]
    assert document.image_url == "https://www.commerce.gov/sites/default/files/img.png"


def test_actor_filters_out_other_news_types() -> None:
    """### Non-report records are counted and dropped."""
    records = [
        commerce_record(),
        commerce_record(record_id="2", news_types=("Fact sheets",), href=report_url("fact", kind="news")),
    ]
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: commerce_page(records)}))

    assert result.articles_discovered == 1
    assert result.records_filtered_out == 1


def test_actor_paginates_by_explicit_offsets() -> None:
    """### Pages are requested by offset on the configured API host."""
    first = commerce_page(
        [
            commerce_record(),
            commerce_record(record_id="2", href=report_url("second")),
        ]
    )
    second = commerce_page([commerce_record(record_id="3", href=report_url("third"))])
    offset_two = page_url(2, 2)
    fetcher = build_fetcher({page_url(2, 0): first, offset_two: second})
    result = build_actor(page_size=2).run(fetcher)

    assert result.listing_pages_attempted == 2
    assert result.articles_discovered == 3
    assert result.pagination_complete is True
    assert fetcher.call_count(offset_two) == 1
    assert all(url.startswith(REPORTS_API_URL) for url in fetcher.calls)


def test_actor_stops_when_offset_pagination_repeats_a_page() -> None:
    """### Repeated API responses cannot keep pagination moving over duplicate data."""
    record = commerce_record()
    repeated = commerce_page([record])
    fetcher = build_fetcher({page_url(1, 0): repeated, page_url(1, 1): repeated})
    result = build_actor(page_size=1, max_pages=3).run(fetcher)
    assert result.articles_discovered == 1
    assert any("repeated a page" in message for message in result.errors)
    assert result.pagination_complete is False


def test_malformed_record_does_not_discard_earlier_documents() -> None:
    """### A timestamp conversion error is isolated to its own API record."""
    valid = commerce_record()
    malformed = commerce_record(record_id="2", href=report_url("broken"), created=10**100)
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: commerce_page([valid, malformed])}))
    assert len(result.documents) == 2
    assert result.documents[0].text is not None
    assert result.documents[1].text is None
    assert result.detail_pages_succeeded == 1
    assert result.detail_pages_failed == 1
    assert result.status == "partial_failure"


def test_actor_phase_exception_keeps_already_accumulated_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    """### A late actor-level failure returns the current result instead of replacing it."""
    actor = build_actor()

    def append_then_fail(fetcher: Any, state: Any, result: Any) -> None:
        result.documents.append(
            CanonicalDocument(
                source="commerce_reports",
                source_url=report_url("kept"),
                title="Already collected",
                language="en",
            )
        )
        raise RuntimeError("unexpected conversion error")

    monkeypatch.setattr(actor, "_materialize", append_then_fail)
    result = actor.run(build_fetcher({FIRST_PAGE_URL: commerce_page([commerce_record()])}))
    assert len(result.documents) == 1
    assert result.documents[0].title == "Already collected"
    assert any("unexpected actor phase failure" in error for error in result.errors)


def test_actor_ignores_a_foreign_pagination_link() -> None:
    """### A next link on another host is never followed."""
    first = commerce_page([commerce_record()], next_page="https://www.commerce.gov/api/news?page[offset]=50")
    result = build_actor(page_size=1, max_pages=1).run(build_fetcher({page_url(1, 0): first}))

    assert result.articles_discovered == 1
    assert any("max_pages" in message for message in result.errors)
    assert result.pagination_complete is False


def test_actor_stops_at_the_page_bound() -> None:
    """### The page bound is honoured and reported as incomplete pagination."""
    record = commerce_record()
    result = build_actor(page_size=1, max_pages=1).run(build_fetcher({page_url(1, 0): commerce_page([record])}))

    assert result.listing_pages_attempted == 1
    assert result.pagination_complete is False
    assert any("max_pages" in message for message in result.errors)


def test_actor_deduplicates_reports_by_url() -> None:
    """### The same report on two pages yields one document."""
    record = commerce_record()
    first = commerce_page([record])
    second = commerce_page([record])
    fetcher = build_fetcher({page_url(1, 0): first, page_url(1, 1): second})
    result = build_actor(page_size=1, max_pages=2).run(fetcher)

    assert len(result.documents) == 1
    assert result.article_urls_deduplicated == 0
    assert any("repeated a page" in message for message in result.errors)


def test_actor_keeps_reports_without_body_as_metadata() -> None:
    """### A report with no body is stored without pretending it has text."""
    record = commerce_record(body=None, teaser=None, subtitle=None)
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: commerce_page([record])}))

    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.text is None
    assert document.content_hash is None
    assert result.records_without_text == 1
    assert result.detail_pages_failed == 1


def test_actor_reports_a_blocked_api() -> None:
    """### An access denial fails the run with the reason recorded."""
    blocked = AccessBlockedError("source refused the request", url=FIRST_PAGE_URL, status_code=403, reason="blocked")
    result = build_actor().run(build_fetcher({}, failures={FIRST_PAGE_URL: blocked}))

    assert result.status == "failed"
    assert result.listing_pages_failed == 1
    assert result.documents == []
    assert any("api fetch failed" in message for message in result.errors)


def test_actor_reports_malformed_json() -> None:
    """### A non-JSON body is a parse failure, never an empty dataset."""
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: b"<html>not json</html>"}))

    assert result.status == "failed"
    assert any("api parse failed" in message for message in result.errors)


def test_actor_fails_when_no_report_matches() -> None:
    """### No matching records is a failure, not a successful empty crawl."""
    record = commerce_record(news_types=("Blog",))
    result = build_actor().run(build_fetcher({FIRST_PAGE_URL: commerce_page([record])}))

    assert result.status == "failed"
    assert result.documents == []
    assert any("no report records matched" in message for message in result.errors)


def test_actor_requires_the_http_engine() -> None:
    """### The API source cannot be served by the rendering engine."""
    assert CommerceReportsActor.requires_http_engine is True


def test_page_url_helpers() -> None:
    """### Page URLs carry both the key and an explicit offset."""
    assert f"{REPORTS_API_URL}?page%5Blimit%5D=50&page%5Boffset%5D=0&api_key=TEST_KEY" == FIRST_PAGE_URL
    assert with_api_key(f"{REPORTS_API_URL}?api_key=OLD&page%5Boffset%5D=50", "NEW").endswith("api_key=NEW")
    assert "api_key=OLD" not in with_api_key(f"{REPORTS_API_URL}?api_key=OLD&x=1", "NEW")


def test_parse_api_page_rejects_bad_payloads() -> None:
    """### Malformed envelopes are reported, never silently empty."""
    with pytest.raises(CommerceApiError, match="not JSON"):
        parse_api_page(b"nope", page_url=FIRST_PAGE_URL, **SCOPE)
    with pytest.raises(CommerceApiError, match="not a JSON object"):
        parse_api_page(b"[]", page_url=FIRST_PAGE_URL, **SCOPE)
    with pytest.raises(CommerceApiError, match="no 'data' array"):
        parse_api_page(b'{"jsonapi": {}}', page_url=FIRST_PAGE_URL, **SCOPE)


def test_parse_api_page_skips_unusable_records() -> None:
    """### Records without identity or outside the scope are skipped and counted."""
    payload = commerce_page(
        [
            commerce_record(),
            commerce_record(record_id="2", href="https://example.com/reports/nope"),
            commerce_record(record_id="3", href="https://www.commerce.gov/about/not-a-report"),
            {"id": "4"},
        ]
    )
    page = parse_api_page(payload, page_url=FIRST_PAGE_URL, **SCOPE)
    assert len(page.records) == 1
    assert page.skipped_records == 3


def test_report_url_scope_is_enforced() -> None:
    """### Only in-scope Commerce report URLs are accepted."""
    assert is_report_url(f"https://{REPORTS_HOST}/reports/2026/01/example", **SCOPE)
    assert not is_report_url("https://example.com/reports/x", **SCOPE)
    assert not is_report_url(f"https://{REPORTS_HOST}/about/history", **SCOPE)


def test_news_type_filter_labels() -> None:
    """### The news type filter reads the API's label list."""
    assert is_news_release(commerce_record(), ("Reports",)) is True
    assert is_news_release(commerce_record(news_types=("Blog",)), ("Reports",)) is False
    assert is_news_release(commerce_record(news_types=("Blog",)), ()) is True
