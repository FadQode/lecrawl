"""### Tests for the CBA news release contract and validators."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from lexcrawl.models.document import TEXT_BEGIN_MARKER, compute_content_hash, ensure_text_marker
from lexcrawl.validation.cba import (
    DOCUMENT_TYPE,
    CbaNewsRelease,
    CbaResource,
    display_titles,
    filename_from_url,
    normalize_topics,
    to_canonical,
    validate_release,
    validate_resource,
)

HOST = "releases.example.test"
URL = f"https://{HOST}/article/sample-release"
PDF_A = f"https://{HOST}/Assets/documents/first.pdf"
PDF_B = f"https://{HOST}/Assets/documents/second.pdf"
EXCEL = f"https://{HOST}/Assets/documents/table.xlsx"
BODY = ensure_text_marker("The release body paragraph.")


def release(**overrides: object) -> CbaNewsRelease:
    """### Build a valid release payload with optional overrides."""
    payload: dict[str, object] = {
        "source": "cba_news_releases",
        "source_url": URL,
        "title": "Sample release title",
        "document_type": DOCUMENT_TYPE,
        "published_at": date(2026, 9, 25),
        "topics": ("#Mortgages",),
        "text": BODY,
        "content_hash": compute_content_hash(BODY),
    }
    payload.update(overrides)
    return CbaNewsRelease.model_validate(payload)


def pdf_resource(**overrides: object) -> CbaResource:
    """### Build a completed PDF resource with optional overrides."""
    text = ensure_text_marker("Extracted pdf text.")
    payload: dict[str, object] = {
        "title": "Sample Data (PDF)",
        "original_url": PDF_A,
        "resource_type": "pdf",
        "mime_type": "application/pdf",
        "file_size": 1234,
        "extracted_text": text,
        "content_hash": compute_content_hash(text),
        "extraction_status": "complete",
    }
    payload.update(overrides)
    return CbaResource.model_validate(payload)


def test_valid_release_passes() -> None:
    """### A complete release with one PDF is valid."""
    report = validate_release(release(resources=(pdf_resource(),)), expected_host=HOST)
    assert report.ok is True
    assert report.resources[0].ok is True
    assert report.issues == ()


def test_title_is_required() -> None:
    """### A blank title is rejected by the contract."""
    with pytest.raises(ValidationError):
        release(title="   ")


def test_invalid_source_url_is_reported() -> None:
    """### A relative or foreign URL fails validation."""
    foreign = release(source_url="https://example.com/article/x")
    codes = {issue.code for issue in validate_release(foreign, expected_host=HOST).article.errors}
    assert "url_out_of_scope" in codes

    with pytest.raises(ValidationError):
        release(source_url="")


def test_invalid_publication_date_is_rejected() -> None:
    """### A non-date publication value cannot enter the contract."""
    with pytest.raises(ValidationError):
        release(published_at="not-a-date")


def test_multiple_topics_are_normalized_and_preserved() -> None:
    """### Topics are stripped, de-duplicated, and keep their page order."""
    release_with_topics = release(topics=("  #Mortgages ", "#Housing", "#Mortgages", ""))
    assert release_with_topics.topics == ("#Mortgages", "#Housing")
    assert normalize_topics("#A, #B") == ("#A, #B",)
    assert normalize_topics(None) == ()


def test_duplicate_topics_are_normalized_away() -> None:
    """### Repeating a topic is normalized rather than duplicated."""
    assert release(topics=("#A", "#B", "#A")).topics == ("#A", "#B")


def test_metadata_only_release_is_valid() -> None:
    """### A release without body text is a valid incomplete record."""
    incomplete = release(text=None, content_hash=None)
    assert incomplete.has_article_text is False
    assert validate_release(incomplete, expected_host=HOST).ok is True


def test_missing_text_marker_is_rejected() -> None:
    """### Article text must begin with the required marker."""
    with pytest.raises(ValidationError):
        release(text="body without marker", content_hash=compute_content_hash("body without marker"))


def test_incorrect_content_hash_is_rejected() -> None:
    """### A hash that does not match the article text is rejected."""
    with pytest.raises(ValidationError):
        release(content_hash="0" * 64)


def test_hash_without_text_is_rejected() -> None:
    """### A hash without text is inconsistent."""
    with pytest.raises(ValidationError):
        release(text=None, content_hash="0" * 64)


def test_excel_resource_needs_no_extracted_text() -> None:
    """### An Excel attachment is metadata only and still valid."""
    excel = CbaResource(
        title="Sample Data (Excel)",
        original_url=EXCEL,
        resource_type="excel",
        extraction_status="not_applicable",
    )
    assert validate_resource(excel, expected_host=HOST).ok is True


def test_multiple_pdfs_get_distinct_display_titles() -> None:
    """### Several PDFs are individually distinguishable by file name."""
    other_text = ensure_text_marker("Other text.")
    second = pdf_resource(
        original_url=PDF_B,
        extracted_text=other_text,
        content_hash=compute_content_hash(other_text),
    )
    titled = display_titles((pdf_resource(), second))

    assert titled[0].display_title == "Sample Data (PDF) [first.pdf]"
    assert titled[1].display_title == "Sample Data (PDF) [second.pdf]"
    assert titled[0].title == titled[1].title == "Sample Data (PDF)"
    assert titled[0].content_hash != titled[1].content_hash


def test_single_pdf_keeps_its_title() -> None:
    """### A single attachment needs no suffix."""
    assert display_titles((pdf_resource(),))[0].display_title is None


def test_filename_is_decoded_from_the_url() -> None:
    """### Display titles use the decoded file name."""
    assert filename_from_url(f"https://{HOST}/Assets/a%20b.pdf") == "a b.pdf"
    assert filename_from_url(None) is None


def test_independent_resource_failures_do_not_touch_the_article() -> None:
    """### One failing attachment leaves the release and the other attachment intact."""
    failed = CbaResource(
        title="Sample Data (PDF)",
        original_url=PDF_B,
        resource_type="pdf",
        extraction_status="failed",
        error="pdf could not be parsed",
    )
    report = validate_release(release(resources=(pdf_resource(), failed)), expected_host=HOST)

    assert report.ok is True
    assert report.resources[0].ok is True
    assert report.resources[1].ok is True


def test_missing_resource_url_is_allowed_when_marked_unavailable() -> None:
    """### A resource without a URL keeps its metadata and states the limitation."""
    unavailable = CbaResource(
        title="Sample Data (PDF)",
        original_url=None,
        resource_type="pdf",
        extraction_status="url_unavailable",
        error="resource URL is not available",
    )
    assert unavailable.original_url is None
    assert validate_resource(unavailable, expected_host=HOST).ok is True

    with pytest.raises(ValidationError):
        CbaResource(title="No url", resource_type="pdf", extraction_status="pending")


def test_duplicate_resources_are_reported() -> None:
    """### The same attachment attached twice is a validation error."""
    report = validate_release(release(resources=(pdf_resource(), pdf_resource())), expected_host=HOST)
    assert "resource_duplicate" in {issue.code for issue in report.issues}


def test_resource_url_scope_is_checked() -> None:
    """### A foreign attachment URL is out of scope."""
    foreign = pdf_resource(original_url="https://example.com/a.pdf")
    assert "url_out_of_scope" in {issue.code for issue in validate_resource(foreign, expected_host=HOST).errors}


def test_completed_resource_requires_marker_and_hash() -> None:
    """### Completed extraction without text or with a wrong hash is rejected."""
    text = ensure_text_marker("text")
    with pytest.raises(ValidationError):
        pdf_resource(extracted_text=None, content_hash=None)
    with pytest.raises(ValidationError):
        pdf_resource(extracted_text="text without marker", content_hash=compute_content_hash(text))
    with pytest.raises(ValidationError):
        pdf_resource(extracted_text=text, content_hash="0" * 64)


def test_non_pdf_with_text_still_requires_consistency() -> None:
    """### Provided text must remain marked and hashed for any resource type."""
    text = ensure_text_marker("sheet text")
    ok = CbaResource(
        title="Sample Data (Excel)",
        original_url=EXCEL,
        resource_type="excel",
        extracted_text=text,
        content_hash=compute_content_hash(text),
        extraction_status="complete",
    )
    assert validate_resource(ok, expected_host=HOST).ok is True


def test_canonical_mapping_preserves_relationships() -> None:
    """### Article text and each resource stay separate but attached."""
    second_text = ensure_text_marker("Second pdf.")
    second = pdf_resource(
        original_url=PDF_B,
        extracted_text=second_text,
        content_hash=compute_content_hash(second_text),
    )
    document = to_canonical(release(resources=display_titles((pdf_resource(), second))))

    assert document.source_url == URL
    assert document.type_of_law == DOCUMENT_TYPE
    assert document.topics == ("#Mortgages",)
    assert document.text == BODY
    assert len(document.resources) == 2
    assert document.resources[0].url == PDF_A
    assert document.resources[1].url == PDF_B
    assert document.resources[0].text.startswith(TEXT_BEGIN_MARKER)
    assert document.resources[0].text != document.text
    assert document.resources[0].content_hash != document.resources[1].content_hash
    assert document.resources[0].status == "complete"
    assert document.resources[0].display_title == "Sample Data (PDF) [first.pdf]"
