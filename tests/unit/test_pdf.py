"""### Unit tests for PDF text extraction."""

from __future__ import annotations

import pymupdf
import pytest

from lexcrawl.pipeline.pdf import PdfExtractionError, extract_pdf_text


def _make_pdf(text: str) -> bytes:
    """### Build a one-page PDF containing text."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def test_extract_round_trip() -> None:
    """### Inserted text is recoverable."""
    assert "Hello PDF" in extract_pdf_text(_make_pdf("Hello PDF"))


def test_empty_payload() -> None:
    """### Empty input is rejected."""
    with pytest.raises(PdfExtractionError):
        extract_pdf_text(b"")


def test_invalid_pdf() -> None:
    """### Non-PDF bytes raise a typed error."""
    with pytest.raises(PdfExtractionError):
        extract_pdf_text(b"definitely not a pdf")
