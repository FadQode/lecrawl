"""### Unit tests for PDF text extraction (no database required)."""

from __future__ import annotations

import pymupdf
import pytest

from lexcrawl.pipeline.pdf import PdfExtractionError, extract_pdf_text


def _make_pdf(text: str) -> bytes:
    """### Build a small one-page PDF containing the given text."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def test_extract_round_trip() -> None:
    """### Text inserted into a PDF is recoverable."""
    assert "Hello PDF" in extract_pdf_text(_make_pdf("Hello PDF"))


def test_empty_payload_raises() -> None:
    """### An empty payload is rejected."""
    with pytest.raises(PdfExtractionError):
        extract_pdf_text(b"")


def test_invalid_pdf_raises() -> None:
    """### Non-PDF bytes raise a typed error."""
    with pytest.raises(PdfExtractionError):
        extract_pdf_text(b"definitely not a pdf")
