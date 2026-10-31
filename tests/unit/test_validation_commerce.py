"""### Contract and validation tests for the Commerce reports source."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from pydantic import ValidationError

from lexcrawl.models.document import (
    TEXT_BEGIN_MARKER,
    CanonicalDocument,
    compute_content_hash,
    ensure_text_marker,
)
from lexcrawl.validation.base import IssueSeverity
from lexcrawl.validation.commerce import (
    MONTH_PRECISION,
    SOURCE_NAME,
    CommerceReport,
    CommerceResource,
    extract_pdf_resource,
    from_api_payload,
    from_listing_row,
    parse_listing_date,
    to_canonical,
    validate_report,
    validate_resource,
)

from ..samples import COMMERCE_LISTING_CAPTURE, COMMERCE_LISTING_URL, commerce_listing_row

HOST = "www.commerce.gov"
REPORT_URL = f"https://{HOST}/data-and-reports/reports/2025/01/sample-commerce-report"
PDF_A = f"https://{HOST}/sites/default/files/2025-01/first.pdf"
PDF_B = f"https://{HOST}/sites/default/files/2025-01/second.pdf"
BODY = ensure_text_marker("The report body paragraph.")


def report(**overrides: object) -> CommerceReport:
    """### Build a valid month-year report payload."""
    base: dict[str, object] = {
        "source_url": REPORT_URL,
        "title": "Sample Commerce report",
        "published_month": 1,
        "published_year": 2025,
        "published_date_raw": "January 2025",
        "published_precision": MONTH_PRECISION,
        "text": BODY,
        "content_hash": compute_content_hash(BODY),
    }
    base.update(overrides)
    return CommerceReport.model_validate(base)


def pdf_resource(**overrides: object) -> CommerceResource:
    """### Build a completed PDF resource payload."""
    text = ensure_text_marker("Extracted pdf text.")
    payload: dict[str, object] = {
        "title": "first.pdf",
        "original_url": PDF_A,
        "resource_type": "pdf",
        "mime_type": "application/pdf",
        "file_size": 1234,
        "extracted_text": text,
        "content_hash": compute_content_hash(text),
        "extraction_status": "complete",
    }
    payload.update(overrides)
    return CommerceResource.model_validate(payload)


@pytest.mark.parametrize(
    ("value", "month", "year"),
    [
        ("January 2025", 1, 2025),
        ("December 2008", 12, 2008),
        ("July 2024", 7, 2024),
        ("  January   2025  ", 1, 2025),
        ("Jan 2025", 1, 2025),
    ],
)
def test_listing_date_parses_month_and_year(value: str, month: int, year: int) -> None:
    """### Month-year values parse without inventing a day."""
    parsed = parse_listing_date(value)
    assert parsed.ok is True
    assert (parsed.month, parsed.year) == (month, year)
    assert parsed.raw == " ".join(value.split())
    assert not hasattr(parsed, "day")


def test_listing_date_missing_is_a_warning() -> None:
    """### An absent publication date is reported, not guessed."""
    for value in (None, "", "   "):
        parsed = parse_listing_date(value)
        assert parsed.month is None and parsed.year is None
        assert [issue.code for issue in parsed.issues] == ["date_missing"]
        assert parsed.issues[0].severity == "warning"


@pytest.mark.parametrize("value", ["Smarch 2025", "2025-13", "January", "13 January 2025", "not a date"])
def test_listing_date_malformed_is_an_error(value: str) -> None:
    """### Malformed and invalid values are blocking findings."""
    parsed = parse_listing_date(value)
    assert parsed.ok is False
    assert parsed.month is None
    assert parsed.issues[0].severity == "error"


def test_report_month_and_year_are_bounded() -> None:
    """### Impossible months and years are rejected by the contract."""
    with pytest.raises(ValidationError):
        report(published_month=13)
    with pytest.raises(ValidationError):
        report(published_year=1500)


def test_month_precision_requires_both_parts() -> None:
    """### Month precision cannot exist with a missing month or year."""
    with pytest.raises(ValidationError):
        report(published_month=None)
    with pytest.raises(ValidationError):
        report(published_year=None)


def test_valid_report_with_one_pdf() -> None:
    """### Scenario A: one report with one PDF is valid."""
    result = report(resources=(pdf_resource(),))
    report_validation = validate_report(result, expected_host=HOST)
    assert report_validation.ok is True
    assert report_validation.resources[0].ok is True
    assert len(result.resources) == 1


def test_valid_report_with_multiple_pdfs() -> None:
    """### Scenario B: many PDFs stay separate resources of one report."""
    other = ensure_text_marker("Second pdf text.")
    result = report(
        resources=(
            pdf_resource(),
            pdf_resource(
                title="second.pdf",
                original_url=PDF_B,
                extracted_text=other,
                content_hash=compute_content_hash(other),
            ),
        )
    )
    assert len(result.resources) == 2
    assert {resource.original_url for resource in result.resources} == {PDF_A, PDF_B}
    assert len({resource.content_hash for resource in result.resources}) == 2


def test_valid_report_without_pdf_uses_page_text() -> None:
    """### Scenario C: a report without attachments keeps its page text."""
    result = report(resources=())
    assert result.resources == ()
    assert result.text is not None and result.text.startswith(TEXT_BEGIN_MARKER)
    assert validate_report(result, expected_host=HOST).ok is True


def test_report_title_and_url_are_required() -> None:
    """### Missing identity fields are rejected."""
    with pytest.raises(ValidationError):
        report(title="   ")
    with pytest.raises(ValidationError):
        report(source_url="")


def test_invalid_report_url_is_reported() -> None:
    """### Out-of-scope report URLs fail validation."""
    foreign = report(source_url="https://example.com/data-and-reports/reports/x")
    codes = {issue.code for issue in validate_report(foreign, expected_host=HOST).article.errors}
    assert "url_out_of_scope" in codes


def test_missing_publication_date_warns() -> None:
    """### A report without a verified date is valid but warns."""
    undated = report(
        published_month=None,
        published_year=None,
        published_date_raw=None,
        published_precision=None,
    )
    validation = validate_report(undated, expected_host=HOST)
    assert validation.ok is True
    assert "date_unavailable" in {issue.code for issue in validation.article.warnings}


def test_date_raw_must_match_the_stored_month_and_year() -> None:
    """### A mismatch between the raw listing date and the stored parts is an error."""
    mismatched = report(published_date_raw="December 2008")
    codes = {issue.code for issue in validate_report(mismatched, expected_host=HOST).article.errors}
    assert "date_mismatch" in codes


def test_text_marker_and_hash_rules() -> None:
    """### Text must be marked and its hash must match."""
    with pytest.raises(ValidationError):
        report(text="unmarked body", content_hash=compute_content_hash("unmarked body"))
    with pytest.raises(ValidationError):
        report(content_hash="0" * 64)
    with pytest.raises(ValidationError):
        report(text=None, content_hash="0" * 64)


def test_valid_pdf_resource() -> None:
    """### A completed PDF validates on its own when attached to a report."""
    assert validate_resource(pdf_resource(), expected_host=HOST, parent=report()).ok is True


def test_resource_without_parent_is_an_error() -> None:
    """### A resource must belong to a report."""
    issues = validate_resource(pdf_resource(), expected_host=HOST, parent=None).errors
    assert "resource_without_parent" in {issue.code for issue in issues}


def test_pdf_without_url_is_allowed_when_marked_unavailable() -> None:
    """### A PDF whose URL cannot be recovered keeps its metadata."""
    unavailable = pdf_resource(
        original_url=None,
        extracted_text=None,
        content_hash=None,
        extraction_status="url_unavailable",
        error="attachment URL is not exposed by the API",
    )
    assert unavailable.original_url is None
    validation = validate_resource(unavailable, expected_host=HOST, parent=report())
    assert validation.ok is True
    assert "resource_url_unavailable" in {issue.code for issue in validation.warnings}

    with pytest.raises(ValidationError):
        pdf_resource(original_url=None, extraction_status="pending")


def test_failed_pdf_extraction_keeps_metadata_and_warns() -> None:
    """### A failed extraction does not invalidate the report."""
    failed = pdf_resource(
        extracted_text=None,
        content_hash=None,
        extraction_status="failed",
        error="pdf could not be parsed",
    )
    parent = report(resources=(pdf_resource(), failed))
    validation = validate_report(parent, expected_host=HOST)

    assert validation.ok is True
    assert validation.resources[0].ok is True
    assert "resource_extraction_failed" in {issue.code for issue in validation.resources[1].warnings}
    assert parent.resources[1].mime_type == "application/pdf"
    assert parent.resources[1].file_size == 1234


def test_pdf_marker_and_hash_are_enforced() -> None:
    """### Invalid markers and hashes are rejected by the resource model."""
    with pytest.raises(ValidationError):
        pdf_resource(extracted_text="unmarked", content_hash=compute_content_hash("unmarked"))
    with pytest.raises(ValidationError):
        pdf_resource(content_hash="0" * 64)


def test_excel_attachment_needs_no_extraction() -> None:
    """### Excel is metadata only and never routed through PDF extraction."""
    excel = CommerceResource(
        title="table.xlsx",
        original_url=f"https://{HOST}/sites/default/files/2025-01/table.xlsx",
        resource_type="excel",
        extraction_status="not_applicable",
    )
    validation = validate_resource(excel, expected_host=HOST, parent=report())
    assert validation.ok is True
    assert validation.warnings == ()


def test_unsupported_attachment_type_warns() -> None:
    """### Other attachment types are preserved with a warning."""
    other = CommerceResource(
        title="notes.txt",
        original_url=f"https://{HOST}/sites/default/files/2025-01/notes.txt",
        resource_type="other",
        extraction_status="not_applicable",
    )
    codes = {issue.code for issue in validate_resource(other, expected_host=HOST, parent=report()).warnings}
    assert "resource_type_unsupported" in codes


def test_duplicate_resources_are_reported() -> None:
    """### The same attachment attached twice is an error."""
    validation = validate_report(report(resources=(pdf_resource(), pdf_resource())), expected_host=HOST)
    assert "resource_duplicate" in {issue.code for issue in validation.issues}


def test_publication_year_keeps_editions_distinct() -> None:
    """### Reports with the same title but different years stay separate."""
    older = report(
        title="Foreign Direct Investment in the United States",
        source_url=f"https://{HOST}/data-and-reports/reports/2017/10/foreign-direct-investment",
        published_month=10,
        published_year=2017,
        published_date_raw="October 2017",
    )
    newer = report(
        title="Foreign Direct Investment in the United States",
        source_url=f"https://{HOST}/data-and-reports/reports/2024/10/foreign-direct-investment",
        published_month=10,
        published_year=2024,
        published_date_raw="October 2024",
    )
    assert older.source_url != newer.source_url
    assert (older.published_year, newer.published_year) == (2017, 2024)
    assert to_canonical(older).source_url != to_canonical(newer).source_url


def test_publication_is_not_derived_from_pdf_filenames() -> None:
    """### A December 2008 report keeps its period even when files mention older years."""
    legacy = report(
        published_month=12,
        published_year=2008,
        published_date_raw="December 2008",
        resources=(
            pdf_resource(
                title="annex-2005.pdf",
                original_url=f"https://{HOST}/sites/default/files/2008-12/annex-2005.pdf",
                extracted_text=None,
                content_hash=None,
                extraction_status="not_applicable",
            ),
            pdf_resource(
                title="annex-2007.pdf",
                original_url=f"https://{HOST}/sites/default/files/2008-12/annex-2007.pdf",
                extracted_text=None,
                content_hash=None,
                extraction_status="not_applicable",
            ),
        ),
    )
    assert legacy.publication_period == "2008-12"
    assert legacy.published_year == 2008
    assert len(legacy.resources) == 2


def test_archive_row_builds_a_report_with_provenance() -> None:
    """### Archived listing rows keep capture provenance separate from the date."""
    row = commerce_listing_row(pdf_urls=(PDF_A, PDF_B))
    archived = from_listing_row(row, listing_capture=COMMERCE_LISTING_CAPTURE, listing_url=COMMERCE_LISTING_URL)

    assert archived.published_month == 1 and archived.published_year == 2025
    assert archived.published_date_raw == "January 2025"
    assert archived.provenance == "wayback"
    assert archived.archive_url and COMMERCE_LISTING_CAPTURE in archived.archive_url
    assert archived.archived_at is not None and archived.archived_at.year == 2026
    assert archived.archived_at.date() != date(2025, 1, 1)
    assert len(archived.resources) == 2
    assert all(resource.extraction_status == "pending" for resource in archived.resources)
    assert all(
        resource.archive_url and COMMERCE_LISTING_CAPTURE in resource.archive_url for resource in archived.resources
    )
    assert validate_report(archived, expected_host=HOST).ok is True


def test_archive_row_without_pdf_has_no_resources() -> None:
    """### A listing row without File(s) yields a report with no attachments."""
    archived = from_listing_row(
        commerce_listing_row(pdf_urls=()),
        listing_capture=COMMERCE_LISTING_CAPTURE,
        listing_url=COMMERCE_LISTING_URL,
    )
    assert archived.resources == ()
    assert validate_report(archived, expected_host=HOST).ok is True


def test_archive_row_without_a_date_warns_but_stays_valid() -> None:
    """### A row without a date keeps unknown and warns."""
    archived = from_listing_row(
        commerce_listing_row(display_date=""),
        listing_capture=COMMERCE_LISTING_CAPTURE,
        listing_url=COMMERCE_LISTING_URL,
    )
    validation = validate_report(archived, expected_host=HOST)
    assert archived.published_month is None
    assert validation.ok is True
    assert "date_unavailable" in {issue.code for issue in validation.article.warnings}


def test_api_payload_keeps_body_text_and_unavailable_attachment_urls() -> None:
    """### API records keep their body text and attachment metadata without URLs."""
    payload: dict[str, object] = {
        "id": "208",
        "label": "Exploring the Digital Nation",
        "href": REPORT_URL,
        "body": "<p>API body paragraph.</p>",
        "created": 1320728400,
        "documents": [
            {
                "id": "1854",
                "title": "exploringthedigitalnation.pdf",
                "filemime": "application/pdf",
                "filesize": "1646999",
                "href": {},
            }
        ],
        "image": {"ImageElements": [{"ImageUrl": "https://www.commerce.gov/sites/default/files/img.png"}]},
    }
    api_report = from_api_payload(payload)

    assert api_report.provenance == "live"
    assert api_report.text is not None and api_report.text.startswith(TEXT_BEGIN_MARKER)
    assert api_report.content_hash == compute_content_hash(api_report.text)
    assert api_report.published_month is None and api_report.published_year is None
    assert api_report.api_created_at is not None
    assert api_report.image_url == "https://www.commerce.gov/sites/default/files/img.png"
    assert len(api_report.resources) == 1
    attachment = api_report.resources[0]
    assert attachment.original_url is None
    assert attachment.extraction_status == "url_unavailable"
    assert attachment.mime_type == "application/pdf"
    assert attachment.file_size == 1_646_999
    assert validate_report(api_report, expected_host=HOST).ok is True


def test_api_payload_without_date_or_body_is_reported() -> None:
    """### An API record with no body and no date is valid but incomplete."""
    api_report = from_api_payload({"id": "1", "label": "Report", "href": REPORT_URL})
    validation = validate_report(api_report, expected_host=HOST)
    assert api_report.text is None
    assert validation.ok is True
    assert {issue.code for issue in validation.article.warnings} >= {"date_unavailable"}


def test_api_and_archive_provenance_are_distinguishable() -> None:
    """### Live and archived records remain clearly labelled."""
    api_report = from_api_payload({"id": "1", "label": "Report", "href": REPORT_URL, "body": "<p>Body</p>"})
    archived = from_listing_row(
        commerce_listing_row(),
        listing_capture=COMMERCE_LISTING_CAPTURE,
        listing_url=COMMERCE_LISTING_URL,
    )
    assert api_report.provenance == "live" and api_report.archive_url is None
    assert archived.provenance == "wayback" and archived.archive_url is not None


def test_canonical_mapping_preserves_precision_and_relationships() -> None:
    """### Month precision and resource links survive canonicalisation."""
    text = ensure_text_marker("Report body.")
    source = report(text=text, content_hash=compute_content_hash(text), resources=(pdf_resource(),))
    document = to_canonical(source)

    assert document.published_at is None
    assert (document.published_month, document.published_year) == (1, 2025)
    assert document.published_date_raw == "January 2025"
    assert document.published_precision == MONTH_PRECISION
    assert document.provenance == "live"
    assert document.text == text
    assert len(document.resources) == 1
    assert document.resources[0].url == PDF_A
    assert document.resources[0].text != document.text
    assert document.resources[0].content_hash
    assert document.resources[0].status == "complete"


def test_canonical_mapping_keeps_archive_provenance() -> None:
    """### Archived provenance and capture timestamps reach the canonical record."""
    archived = from_listing_row(
        commerce_listing_row(),
        listing_capture=COMMERCE_LISTING_CAPTURE,
        listing_url=COMMERCE_LISTING_URL,
    )
    document = to_canonical(archived)
    assert document.provenance == "wayback"
    assert document.archive_url and COMMERCE_LISTING_CAPTURE in document.archive_url
    assert document.archive_listing_url and COMMERCE_LISTING_URL in document.archive_listing_url
    assert isinstance(document.archived_at, datetime)
    assert document.resources[0].archive_url is not None
    assert document.resources[0].archived_at == document.archived_at


def test_canonical_defaults_keep_other_sources_compatible() -> None:
    """### Documents without new fields still validate and serialize."""
    plain = CanonicalDocument(source="other", source_url="https://example.test/x", title="T", language="en")
    payload = plain.model_dump(mode="json")
    assert payload["published_at"] is None
    assert payload["published_month"] is None
    assert payload["published_precision"] is None
    assert payload["provenance"] == "live"
    assert payload["archive_url"] is None
    assert CanonicalDocument.model_validate(payload) == plain


def test_serialization_round_trip_keeps_month_precision() -> None:
    """### A month-year document round-trips without gaining a day."""
    document = to_canonical(report())
    payload = document.model_dump(mode="json")
    restored = CanonicalDocument.model_validate(payload)
    assert restored.published_at is None
    assert (restored.published_month, restored.published_year) == (1, 2025)
    assert restored.published_precision == "month"


def test_source_is_fixed() -> None:
    """### The contract identifies the source."""
    assert report().source == SOURCE_NAME
    with pytest.raises(ValidationError):
        report(source="cba_news_releases")


def test_issue_severity_values_are_shared() -> None:
    """### Severities reuse the shared literal."""
    severities: set[IssueSeverity] = {"error", "warning"}
    assert severities == {"error", "warning"}


def build_pdf(text: str) -> bytes:
    """### Build a real in-memory PDF for extraction tests."""
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


def test_pdf_extraction_helper_completes_the_resource() -> None:
    """### Scenario A: extraction fills the resource text, hash, and size."""
    pending = CommerceResource(title="first.pdf", original_url=PDF_A, resource_type="pdf", extraction_status="pending")
    completed = extract_pdf_resource(pending, build_pdf("Report statistics table"))

    assert completed.extraction_status == "complete"
    assert completed.extracted_text is not None and completed.extracted_text.startswith(TEXT_BEGIN_MARKER)
    assert completed.content_hash == compute_content_hash(completed.extracted_text)
    assert completed.mime_type == "application/pdf"
    assert completed.file_size and completed.file_size > 0
    assert completed.original_url == PDF_A
    assert validate_resource(completed, expected_host=HOST, parent=report()).ok is True


def test_pdf_extraction_failure_keeps_metadata_and_never_stores_text() -> None:
    """### A corrupt payload yields a failed resource without losing its identity."""
    pending = CommerceResource(
        title="broken.pdf",
        original_url=PDF_B,
        resource_type="pdf",
        mime_type="application/pdf",
        file_size=99,
        extraction_status="pending",
    )
    failed = extract_pdf_resource(pending, b"%PDF-1.7 broken body")

    assert failed.extraction_status == "failed"
    assert failed.extracted_text is None
    assert failed.content_hash is None
    assert failed.mime_type == "application/pdf"
    assert failed.file_size == 99
    assert failed.error
    attached = report(resources=(failed,))
    validation = validate_report(attached, expected_host=HOST)
    assert validation.ok is True
    assert "resource_extraction_failed" in {issue.code for issue in validation.resources[0].warnings}


def test_only_pdf_resources_are_extracted() -> None:
    """### Excel attachments are never routed through PDF extraction."""
    excel = CommerceResource(
        title="table.xlsx",
        original_url=f"https://{HOST}/sites/default/files/2025-01/table.xlsx",
        resource_type="excel",
        extraction_status="not_applicable",
    )
    with pytest.raises(ValueError):
        extract_pdf_resource(excel, b"fake")


def test_multiple_pdfs_extract_independently() -> None:
    """### Scenario B: one extraction failure does not affect the sibling."""
    first = extract_pdf_resource(
        CommerceResource(title="a.pdf", original_url=PDF_A, resource_type="pdf"),
        build_pdf("First annex"),
    )
    second = extract_pdf_resource(
        CommerceResource(title="b.pdf", original_url=PDF_B, resource_type="pdf"),
        b"not a pdf",
    )
    attached = report(resources=(first, second))
    validation = validate_report(attached, expected_host=HOST)

    assert validation.ok is True
    assert [resource.extraction_status for resource in attached.resources] == ["complete", "failed"]
    assert attached.resources[0].extracted_text != attached.resources[1].extracted_text
    assert attached.resources[0].extracted_text is not None
    assert attached.resources[1].extracted_text is None
