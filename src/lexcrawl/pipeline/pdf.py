"""### PDF text extraction via PyMuPDF."""

from __future__ import annotations

import pymupdf


class PdfExtractionError(ValueError):
    """### Raised when a PDF cannot be turned into text."""


def extract_pdf_text(data: bytes) -> str:
    """### Extract page text from a PDF in page order, dropping empty pages."""
    if not data:
        raise PdfExtractionError("empty PDF payload")
    try:
        with pymupdf.open(stream=data, filetype="pdf") as document:  # type: ignore[no-untyped-call]
            pages = [page.get_text("text").strip() for page in document]
    except Exception as error:  # $ PyMuPDF raises varied errors for malformed input.$
        raise PdfExtractionError(f"PDF could not be read: {error}") from error
    return "\n\n".join(page for page in pages if page)
