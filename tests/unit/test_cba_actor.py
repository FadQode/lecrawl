"""### Pipeline tests for the CBA news releases actor."""

from __future__ import annotations

import pymupdf
import pytest

from lexcrawl.actors.cba import CbaNewsReleasesActor, parse_listing
from lexcrawl.crawling.base import AccessBlockedError, FetchError
from lexcrawl.models.document import TEXT_BEGIN_MARKER

from ..conftest import StubFetcher
from ..samples import (
    RELEASES_API_URL,
    RELEASES_ARTICLE_PATH,
    RELEASES_EXCEL_URL,
    RELEASES_OTHER_TYPE_ID,
    RELEASES_PDF_URL,
    RELEASES_SECTIONS,
    RELEASES_SITE_URL,
    RELEASES_TYPE_ID,
    cba_article_html,
    cba_listing_page,
    cba_listing_record,
)

ARTICLE_URL = f"{RELEASES_SITE_URL}{RELEASES_ARTICLE_PATH}sample-release"
SECOND_ARTICLE_URL = f"{RELEASES_SITE_URL}{RELEASES_ARTICLE_PATH}release-two"
PDF_TARGET = f"{RELEASES_SITE_URL}{RELEASES_PDF_URL}"
EXCEL_TARGET = f"{RELEASES_SITE_URL}{RELEASES_EXCEL_URL}"


def build_pdf(text: str = "Mortgage arrears data table") -> bytes:
    """### Build a real in-memory PDF for resource tests."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


def build_fetcher(
    *,
    listing_pages: list[bytes] | None = None,
    article_html: bytes | None = None,
    article_failures: dict[str, Exception] | None = None,
    pdf_payload: bytes | None = None,
    pdf_failure: Exception | None = None,
) -> StubFetcher:
    """### Serve listing pages, article pages, and PDF payloads offline."""
    listings = listing_pages or [cba_listing_page([cba_listing_record()])]
    pages: dict[str, bytes] = {}
    for listing in listings:
        for record in parse_listing(
            listing,
            api_url=RELEASES_API_URL,
            site_url=RELEASES_SITE_URL,
            article_path=RELEASES_ARTICLE_PATH,
        ).records:
            pages.setdefault(record.url, article_html if article_html is not None else cba_article_html())
    failures = dict(article_failures or {})
    if pdf_failure is not None:
        failures[PDF_TARGET] = pdf_failure
    else:
        pages[PDF_TARGET] = pdf_payload if pdf_payload is not None else build_pdf()
    return StubFetcher(
        pages,
        failures=failures,
        content_type="application/json",
        post_responses=listings,
    )


def build_actor(
    *,
    page_size: int = 50,
    max_pages: int = 2,
    type_guid: str = RELEASES_TYPE_ID,
) -> CbaNewsReleasesActor:
    """### Build the actor with test defaults."""
    return CbaNewsReleasesActor(
        api_url=RELEASES_API_URL,
        site_url=RELEASES_SITE_URL,
        api_language="en-us",
        type_guid=type_guid,
        article_path=RELEASES_ARTICLE_PATH,
        page_size=page_size,
        max_pages=max_pages,
    )


def test_actor_enriches_a_news_release() -> None:
    """### A discovered release becomes a document with text and resources."""
    result = build_actor().run(build_fetcher())

    assert result.listing_pages_succeeded == 1
    assert result.articles_discovered == 1
    assert result.detail_pages_succeeded == 1
    assert result.pagination_complete is True
    assert result.status == "success"
    assert len(result.documents) == 1

    document = result.documents[0]
    assert document.source == "cba_news_releases"
    assert document.source_url == ARTICLE_URL
    assert document.type_of_law == "News Releases"
    assert document.topics == ("#Mortgages",)
    assert document.text is not None and document.text.startswith(TEXT_BEGIN_MARKER)
    assert document.summary is not None
    assert len(document.resources) == 2

    pdf, excel = document.resources
    assert pdf.url == PDF_TARGET
    assert pdf.kind == "pdf"
    assert pdf.text is not None and pdf.text.startswith(TEXT_BEGIN_MARKER)
    assert pdf.error is None
    assert excel.url == EXCEL_TARGET
    assert excel.kind == "excel"
    assert excel.text is None
    assert excel.error is None


def test_actor_stores_no_pdf_binary() -> None:
    """### Only extracted PDF text and its URL are retained."""
    result = build_actor().run(build_fetcher())
    payload = result.documents[0].model_dump(mode="json")
    serialized = str(payload)
    assert "%PDF-" not in serialized
    assert "Mortgage arrears data table" in serialized


def test_actor_filters_out_other_article_types() -> None:
    """### Records that are not news releases are counted and dropped."""
    listing = cba_listing_page(
        [
            cba_listing_record(),
            cba_listing_record(slug="a-remark", type_ids=(RELEASES_OTHER_TYPE_ID,)),
        ]
    )
    result = build_actor().run(build_fetcher(listing_pages=[listing]))

    assert result.articles_discovered == 1
    assert result.records_filtered_out == 1
    assert len(result.documents) == 1


def test_actor_pages_through_the_listing_feed() -> None:
    """### Listing pages are requested with the documented offsets."""
    first = cba_listing_page([cba_listing_record(), cba_listing_record(slug="release-two")])
    second = cba_listing_page([cba_listing_record(slug="release-three")])
    fetcher = build_fetcher(listing_pages=[first, second])
    result = build_actor(page_size=2).run(fetcher)

    assert result.listing_pages_attempted == 2
    assert result.articles_discovered == 3
    assert result.pagination_complete is True
    assert '"SkipRecords": 0' in fetcher.posts[0][1]
    assert '"SkipRecords": 2' in fetcher.posts[1][1]


def test_actor_stops_at_the_page_bound() -> None:
    """### The page bound is honoured and reported as incomplete pagination."""
    full_page = cba_listing_page([cba_listing_record(slug=f"release-{index}") for index in range(2)])
    result = build_actor(page_size=2, max_pages=1).run(build_fetcher(listing_pages=[full_page]))

    assert result.listing_pages_attempted == 1
    assert result.pagination_complete is False
    assert any("max_pages" in error for error in result.errors)


def test_actor_deduplicates_across_listing_pages() -> None:
    """### The same release repeated across pages yields one document."""
    page = cba_listing_page([cba_listing_record(), cba_listing_record()])
    result = build_actor(page_size=2, max_pages=2).run(build_fetcher(listing_pages=[page, cba_listing_page([])]))

    assert result.articles_discovered == 1
    assert result.article_urls_deduplicated == 1
    assert result.pagination_complete is True


def test_actor_keeps_metadata_when_enrichment_fails() -> None:
    """### A failed article fetch still stores the discovery metadata."""
    fetcher = build_fetcher(article_failures={ARTICLE_URL: FetchError("connection reset", url=ARTICLE_URL)})
    result = build_actor().run(fetcher)

    assert result.status == "partial_failure"
    assert result.detail_pages_failed == 1
    assert result.records_without_text == 1
    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.text is None
    assert document.content_hash is None
    assert document.source_url == ARTICLE_URL
    assert document.title == "Sample release title for the listings"
    assert document.type_of_law == "News Releases"
    assert any("article fetch failed" in error for error in result.errors)


def test_actor_keeps_metadata_when_the_page_lacks_a_body() -> None:
    """### A page without a body is reported and stored as metadata only."""
    fetcher = build_fetcher(article_html=cba_article_html(with_body=False))
    result = build_actor().run(fetcher)

    assert result.records_missing_required_fields == 1
    assert result.records_without_text == 1
    assert result.documents[0].text is None
    assert any("article parse failed" in error for error in result.errors)


def test_actor_reports_pdf_failures_without_losing_the_article() -> None:
    """### An inaccessible PDF is recorded while the article stays enriched."""
    fetcher = build_fetcher(pdf_failure=FetchError("pdf download failed", url=PDF_TARGET))
    result = build_actor().run(fetcher)

    assert result.detail_pages_succeeded == 1
    pdf = result.documents[0].resources[0]
    assert pdf.text is None
    assert pdf.error is not None
    assert any("pdf processing failed" in error for error in result.errors)


def test_actor_reports_malformed_pdf_content() -> None:
    """### A malformed PDF is reported and no text is attached."""
    result = build_actor().run(build_fetcher(pdf_payload=b"%PDF-1.7\nbroken body"))

    pdf = result.documents[0].resources[0]
    assert pdf.text is None
    assert pdf.error is not None
    assert result.documents[0].text is not None


def test_actor_fails_when_the_listing_is_blocked() -> None:
    """### A blocked listing feed fails the run with the reason recorded."""
    blocked = AccessBlockedError("source refused the request", url=RELEASES_API_URL, status_code=403, reason="blocked")
    result = build_actor().run(StubFetcher({}, failures={RELEASES_API_URL: blocked}))

    assert result.status == "failed"
    assert result.listing_pages_failed == 1
    assert result.documents == []
    assert any("listing fetch failed" in error for error in result.errors)


def test_actor_fails_when_no_release_matches() -> None:
    """### An empty news-release set is a failure, not a successful empty crawl."""
    listing = cba_listing_page([cba_listing_record(slug="a-remark", type_ids=(RELEASES_OTHER_TYPE_ID,))])
    result = build_actor().run(build_fetcher(listing_pages=[listing]))

    assert result.status == "failed"
    assert result.documents == []
    assert any("no news releases matched" in error for error in result.errors)


def test_repeated_runs_produce_the_same_records_without_duplicates() -> None:
    """### Running twice yields the same unique rows, keyed by canonical URL."""
    first = build_actor().run(build_fetcher())
    second = build_actor().run(build_fetcher())

    first_urls = [document.source_url for document in first.documents]
    second_urls = [document.source_url for document in second.documents]
    assert first_urls == second_urls
    assert len(set(first_urls)) == len(first_urls)
    assert first.documents[0].content_hash == second.documents[0].content_hash


def test_body_sections_are_all_collected() -> None:
    """### Every content section is extracted beyond the first screen."""
    result = build_actor().run(build_fetcher())
    text = result.documents[0].text or ""
    for heading, paragraph in RELEASES_SECTIONS:
        assert heading in text
        assert paragraph in text


def test_actor_requires_the_http_engine() -> None:
    """### The listing feed is posted, so the rendering engine cannot serve it."""
    assert CbaNewsReleasesActor.requires_http_engine is True


def test_actor_declares_the_news_release_type_guid() -> None:
    """### The configured type identifier matches the verified live value."""
    actor = build_actor()
    assert actor.type_guid == RELEASES_TYPE_ID


def test_actor_rejects_a_fetcher_without_posting_support() -> None:
    """### A strategy that cannot post JSON surfaces the failure to the caller."""
    from lexcrawl.crawling.browser import BrowserFetcher

    fetcher = BrowserFetcher(user_agent="LexCrawl-test/0.1", respect_robots_txt=False)
    with pytest.raises(FetchError, match="cannot post JSON"):
        fetcher.post_json(RELEASES_API_URL, payload="{}")
