"""### PDF extraction tests for linked resources."""

from __future__ import annotations

import time

import pymupdf
import pytest

from lexcrawl.pipeline.pdf import (
    PdfExtractionError,
    _run_bounded_extraction,
    extract_pdf,
    extract_pdf_text,
    is_pdf_resource,
    resource_kind,
)

LIMIT = 1_000_000


def build_pdf(*pages: str) -> bytes:
    """### Build a real in-memory PDF with the given page texts."""
    document = pymupdf.open()
    for text in pages or ("",):
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    payload = document.tobytes()
    document.close()
    return payload


def slow_extractor(payload: bytes) -> str:
    """### Simulate a parser that exceeds its enforced time limit."""
    time.sleep(5)
    return "too late"


def test_pdf_text_is_extracted() -> None:
    """### Page text is extracted from a real PDF payload."""
    text = extract_pdf_text(build_pdf("Mortgage arrears statistics 2026"), max_bytes=LIMIT)
    assert "Mortgage arrears statistics 2026" in text


def test_multi_page_pdf_text_is_joined() -> None:
    """### Every page contributes to the extracted text."""
    text = extract_pdf_text(build_pdf("First page", "Second page"), max_bytes=LIMIT)
    assert "First page" in text
    assert "Second page" in text


def test_structured_result_preserves_page_boundaries_and_metadata() -> None:
    """### Structured output records normalized text, counts, hash, and status."""
    result = extract_pdf(build_pdf("First page", "Second page"), max_bytes=LIMIT)

    assert result.extraction_status == "complete"
    assert result.extracted_text is not None
    assert "First page\n\nSecond page" in result.extracted_text
    assert result.page_count == 2
    assert result.character_count == len(result.extracted_text)
    assert result.content_hash


def test_structured_result_reports_failures_without_empty_success() -> None:
    """### Malformed content becomes an explicit failed result with a reason."""
    result = extract_pdf(b"%PDF-1.7 invalid", max_bytes=LIMIT)

    assert result.extraction_status == "failed"
    assert result.extracted_text is None
    assert result.content_hash is None
    assert result.error_message


def test_pdf_without_text_is_reported() -> None:
    """### A PDF with no extractable text raises instead of returning empty text."""
    with pytest.raises(PdfExtractionError, match="no extractable text"):
        extract_pdf_text(build_pdf(), max_bytes=LIMIT)


def test_encrypted_pdf_returns_an_explicit_failure() -> None:
    """### Encrypted documents that cannot be opened are reported as failed."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Secret report")
    payload = document.tobytes(
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-password",
        user_pw="user-password",
    )
    document.close()

    result = extract_pdf(payload, max_bytes=LIMIT)

    assert result.extraction_status == "failed"
    assert result.extracted_text is None
    assert result.error_message


def test_empty_payload_is_rejected() -> None:
    """### An empty payload is rejected."""
    with pytest.raises(PdfExtractionError, match="empty"):
        extract_pdf_text(b"", max_bytes=LIMIT)
    with pytest.raises(PdfExtractionError, match="empty"):
        extract_pdf_text(b"   ", max_bytes=LIMIT)


def test_non_pdf_payload_is_rejected() -> None:
    """### A payload without a PDF header is rejected."""
    with pytest.raises(PdfExtractionError, match="PDF header"):
        extract_pdf_text(b"<html>not a pdf</html>", max_bytes=LIMIT)


def test_malformed_pdf_is_reported() -> None:
    """### A truncated PDF surfaces as a typed extraction error."""
    with pytest.raises(PdfExtractionError):
        extract_pdf_text(b"%PDF-1.7\nthis is not a real pdf body", max_bytes=LIMIT)


def test_oversized_pdf_is_rejected() -> None:
    """### A payload above the configured limit is refused."""
    payload = build_pdf("oversized")
    with pytest.raises(PdfExtractionError, match="limit"):
        extract_pdf_text(payload, max_bytes=len(payload) - 1)


def test_pdf_extraction_timeout_terminates_the_worker() -> None:
    """### A timed-out parser process is terminated instead of keeping the caller blocked."""
    started = time.monotonic()
    with pytest.raises(PdfExtractionError, match="timed out"):
        _run_bounded_extraction(b"%PDF-test", timeout_seconds=0.5, extractor=slow_extractor)
    assert time.monotonic() - started < 3


def test_resource_kinds_are_classified_by_extension() -> None:
    """### Formats are classified from the URL, including query strings."""
    assert resource_kind("https://files.example.test/Assets/x/report.pdf") == "pdf"
    assert resource_kind("https://files.example.test/Assets/x/report.pdf?download=1") == "pdf"
    assert resource_kind("https://files.example.test/Assets/x/report.xlsx") == "excel"
    assert resource_kind("https://files.example.test/Assets/x/report.xls") == "excel"
    assert resource_kind("https://files.example.test/Assets/x/report.docx") == "word"
    assert resource_kind("https://files.example.test/article/sample-release") == "other"
    assert is_pdf_resource("https://files.example.test/Assets/x/report.PDF") is True
    assert is_pdf_resource("https://files.example.test/Assets/x/report.xlsx") is False
