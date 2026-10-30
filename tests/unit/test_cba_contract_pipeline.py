"""### Integration tests for the CBA contract inside the crawling pipeline."""

from __future__ import annotations

import pymupdf

from lexcrawl.actors.cba import CbaNewsReleasesActor
from lexcrawl.crawling.base import AccessBlockedError, FetchError
from lexcrawl.models.document import TEXT_BEGIN_MARKER

from ..conftest import StubFetcher
from ..samples import (
    RELEASES_API_URL,
    RELEASES_ARTICLE_PATH,
    RELEASES_EXCEL_URL,
    RELEASES_PDF_URL,
    RELEASES_SITE_URL,
    RELEASES_TYPE_ID,
    cba_article_html,
    cba_listing_page,
    cba_listing_record,
)

ARTICLE_URL = f"{RELEASES_SITE_URL}{RELEASES_ARTICLE_PATH}sample-release"
PDF_A = f"{RELEASES_SITE_URL}{RELEASES_PDF_URL}"
PDF_B = f"{RELEASES_SITE_URL}/Assets/documents/statistics/second.pdf"
EXCEL = f"{RELEASES_SITE_URL}{RELEASES_EXCEL_URL}"


def build_pdf(text: str) -> bytes:
    """### Build a real in-memory PDF with the given text."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


def build_actor() -> CbaNewsReleasesActor:
    """### Build the actor against the sample source configuration."""
    return CbaNewsReleasesActor(
        api_url=RELEASES_API_URL,
        site_url=RELEASES_SITE_URL,
        api_language="en-us",
        type_guid=RELEASES_TYPE_ID,
        article_path=RELEASES_ARTICLE_PATH,
        page_size=50,
        max_pages=2,
    )


def build_fetcher(
    *,
    article_html: bytes | None = None,
    pdf_payloads: dict[str, bytes] | None = None,
    failures: dict[str, Exception] | None = None,
) -> StubFetcher:
    """### Serve the listing, the article page, and any PDF payloads."""
    listing = cba_listing_page([cba_listing_record()])
    pages: dict[str, bytes] = {ARTICLE_URL: article_html if article_html is not None else cba_article_html()}
    for url, payload in (pdf_payloads or {PDF_A: build_pdf("First pdf body")}).items():
        pages[url] = payload
    return StubFetcher(pages, failures=failures or {}, content_type="application/json", post_responses=[listing])


def test_actor_maps_a_release_with_one_pdf() -> None:
    """### The article and its PDF become separate, linked canonical fields."""
    document = build_actor().run(build_fetcher()).documents[0]

    assert document.source == "cba_news_releases"
    assert document.source_url == ARTICLE_URL
    assert document.type_of_law == "News Releases"
    assert document.topics == ("#Mortgages",)
    assert document.text is not None and document.text.startswith(TEXT_BEGIN_MARKER)

    pdfs = [resource for resource in document.resources if resource.kind == "pdf"]
    assert len(pdfs) == 1
    pdf = pdfs[0]
    assert pdf.url == PDF_A
    assert pdf.kind == "pdf"
    assert pdf.status == "complete"
    assert pdf.mime_type == "application/pdf"
    assert pdf.file_size and pdf.file_size > 0
    assert pdf.text is not None and pdf.text.startswith(TEXT_BEGIN_MARKER)
    assert pdf.content_hash
    assert pdf.text != document.text
    assert pdf.error is None


def test_actor_handles_multiple_pdfs_independently() -> None:
    """### Each PDF keeps its own URL, text, hash, and distinguishable title."""
    article = cba_article_html(pdf_urls=(RELEASES_PDF_URL, "/Assets/documents/statistics/second.pdf"))
    fetcher = build_fetcher(
        article_html=article,
        pdf_payloads={PDF_A: build_pdf("First pdf body"), PDF_B: build_pdf("Second pdf body")},
    )
    document = build_actor().run(fetcher).documents[0]

    pdfs = [resource for resource in document.resources if resource.kind == "pdf"]
    assert len(pdfs) == 2
    first, second = pdfs
    assert first.url == PDF_A
    assert second.url == PDF_B
    assert first.text != second.text
    assert first.content_hash != second.content_hash
    assert first.display_title and "sample-data.pdf" in first.display_title
    assert second.display_title and "second.pdf" in second.display_title
    assert first.display_title.startswith(first.title)
    assert second.display_title.startswith(second.title)
    assert first.display_title != second.display_title


def test_actor_keeps_the_article_when_one_pdf_fails() -> None:
    """### One failing attachment does not discard the other results."""
    article = cba_article_html(pdf_urls=(RELEASES_PDF_URL, "/Assets/documents/statistics/second.pdf"))
    fetcher = build_fetcher(
        article_html=article,
        pdf_payloads={PDF_A: build_pdf("First pdf body")},
        failures={PDF_B: FetchError("connection reset", url=PDF_B)},
    )
    result = build_actor().run(fetcher)
    document = result.documents[0]

    pdfs = [resource for resource in document.resources if resource.kind == "pdf"]
    assert document.text is not None
    assert [resource.status for resource in pdfs] == ["complete", "failed"]
    assert pdfs[0].text is not None
    assert pdfs[1].text is None
    assert pdfs[1].error is not None
    assert any("pdf processing failed" in message for message in result.errors)
    assert result.records_without_text == 0


def test_actor_marks_a_missing_pdf_as_url_unavailable() -> None:
    """### A 404 attachment is recorded as unavailable, not as a parse failure."""
    not_found = FetchError("unexpected status 404", url=PDF_A, status_code=404)
    document = build_actor().run(build_fetcher(failures={PDF_A: not_found})).documents[0]

    pdf = next(resource for resource in document.resources if resource.kind == "pdf")
    assert pdf.status == "url_unavailable"
    assert pdf.text is None
    assert document.text is not None


def test_actor_marks_a_blocked_pdf() -> None:
    """### A denied attachment is recorded separately from the article."""
    blocked = AccessBlockedError("source refused the request", url=PDF_A, status_code=403, reason="blocked")
    document = build_actor().run(build_fetcher(failures={PDF_A: blocked})).documents[0]

    pdf = next(resource for resource in document.resources if resource.kind == "pdf")
    assert pdf.status == "access_blocked"
    assert document.text is not None


def test_actor_preserves_excel_without_extraction() -> None:
    """### An Excel attachment is kept as metadata with no extracted text."""
    document = build_actor().run(build_fetcher()).documents[0]

    excel = next(resource for resource in document.resources if resource.kind == "excel")
    assert excel.url == EXCEL
    assert excel.status == "not_applicable"
    assert excel.text is None


def test_actor_deduplicates_repeated_attachment_links() -> None:
    """### The same attachment linked twice is processed once."""
    article = cba_article_html(pdf_urls=(RELEASES_PDF_URL, RELEASES_PDF_URL))
    document = build_actor().run(build_fetcher(article_html=article)).documents[0]

    assert len([resource for resource in document.resources if resource.kind == "pdf"]) == 1


def test_actor_keeps_metadata_when_the_article_body_is_missing() -> None:
    """### A release without a body is stored as an incomplete record."""
    result = build_actor().run(build_fetcher(article_html=cba_article_html(with_body=False)))
    document = result.documents[0]

    assert document.text is None
    assert document.content_hash is None
    assert document.source_url == ARTICLE_URL
    assert document.title
    assert result.records_without_text == 1
    assert result.records_missing_required_fields == 1


def test_actor_keeps_resources_attached_to_their_article() -> None:
    """### The canonical resource list belongs to the release that referenced it."""
    document = build_actor().run(build_fetcher()).documents[0]
    assert all(resource.url for resource in document.resources)
    assert EXCEL in {resource.url for resource in document.resources}
    assert PDF_A in {resource.url for resource in document.resources}
