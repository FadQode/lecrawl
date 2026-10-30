"""### PDF text extraction for resources linked from a source page."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import PurePosixPath

import pymupdf

from lexcrawl.pipeline.normalize import collapse_whitespace

PDF_MAGIC = b"%PDF-"
_EXTENSION_KINDS = {
    ".pdf": "pdf",
    ".xls": "excel",
    ".xlsx": "excel",
    ".csv": "csv",
    ".doc": "word",
    ".docx": "word",
}


class PdfExtractionError(ValueError):
    """### Raised when a linked PDF cannot be converted into usable text."""


def resource_kind(url: str) -> str:
    """### Classify a linked resource by its file extension."""
    path = url.split("?", 1)[0].split("#", 1)[0]
    return _EXTENSION_KINDS.get(PurePosixPath(path).suffix.lower(), "other")


def is_pdf_resource(url: str) -> bool:
    """### Report whether a linked resource is a PDF."""
    return resource_kind(url) == "pdf"


def extract_pdf_text(payload: bytes, *, max_bytes: int, timeout_seconds: float = 60.0) -> str:
    """### Extract the text of a PDF payload, or raise a typed failure."""
    if not payload.strip():
        raise PdfExtractionError("pdf payload is empty")
    if len(payload) > max_bytes:
        raise PdfExtractionError(f"pdf exceeds the {max_bytes} byte limit")
    if not payload.startswith(PDF_MAGIC):
        raise PdfExtractionError("payload does not start with a PDF header")

    # $ Bound the extraction so a hostile document cannot stall the crawl.$
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_read_document_text, payload)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeoutError as error:
            raise PdfExtractionError(f"pdf extraction timed out after {timeout_seconds}s") from error


def _read_document_text(payload: bytes) -> str:
    """### Read every page of a PDF and join the extracted lines."""
    try:
        with pymupdf.open(stream=payload, filetype="pdf") as document:
            pages = [page.get_text("text") for page in document]
    except Exception as error:  # $ Any parser failure surfaces as one typed extraction error.$
        raise PdfExtractionError(f"pdf could not be parsed: {error}") from error
    text = "\n".join(collapse_whitespace(page) for page in pages if page.strip())
    if not text.strip():
        raise PdfExtractionError("pdf contains no extractable text")
    return text
