"""### PDF text extraction for resources linked from a source page."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import get_context
from multiprocessing.connection import Connection
from pathlib import PurePosixPath

import pymupdf

from lexcrawl.models.document import ResourceKind, ResourceStatus, compute_content_hash, ensure_text_marker
from lexcrawl.pipeline.normalize import collapse_whitespace

PDF_MAGIC = b"%PDF-"
_EXTENSION_KINDS: dict[str, ResourceKind] = {
    ".pdf": "pdf",
    ".xls": "excel",
    ".xlsx": "excel",
    ".csv": "csv",
    ".doc": "word",
    ".docx": "word",
}


class PdfExtractionError(ValueError):
    """### Raised when a linked PDF cannot be converted into usable text."""


@dataclass(frozen=True, slots=True)
class PdfExtractionResult:
    """### Structured outcome for one bounded PDF text extraction."""

    extracted_text: str | None
    page_count: int
    character_count: int
    content_hash: str | None
    extraction_status: ResourceStatus
    error_message: str | None = None


def resource_kind(url: str) -> ResourceKind:
    """### Classify a linked resource by its file extension."""
    path = url.split("?", 1)[0].split("#", 1)[0]
    return _EXTENSION_KINDS.get(PurePosixPath(path).suffix.lower(), "other")


def is_pdf_resource(url: str) -> bool:
    """### Report whether a linked resource is a PDF."""
    return resource_kind(url) == "pdf"


def is_pdf_content_type(value: str | None) -> bool:
    """### Accept PDF media types and generic binary data for signature validation."""
    if value is None:
        return True
    media_type = value.split(";", 1)[0].strip().lower()
    return media_type in {"application/pdf", "application/x-pdf", "application/octet-stream"}


def extract_pdf(
    payload: bytes,
    *,
    max_bytes: int,
    timeout_seconds: float = 60.0,
) -> PdfExtractionResult:
    """### Extract and normalize PDF pages, returning success or an explicit failure."""
    try:
        if not payload.strip():
            raise PdfExtractionError("pdf payload is empty")
        if len(payload) > max_bytes:
            raise PdfExtractionError(f"pdf exceeds the {max_bytes} byte limit")
        if not payload.startswith(PDF_MAGIC):
            raise PdfExtractionError("payload does not start with a PDF header")
        extracted = _run_bounded_extraction(
            payload,
            timeout_seconds=timeout_seconds,
            extractor=_read_document_text,
        )
        if not isinstance(extracted, tuple) or len(extracted) != 2:
            raise PdfExtractionError("pdf extraction worker returned an invalid result")
        raw_text, page_count = extracted
        text = "\n\n".join(collapse_whitespace(page) for page in raw_text.split("\f") if page.strip())
        if not text:
            raise PdfExtractionError("pdf contains no extractable text")
        marked = ensure_text_marker(text)
        return PdfExtractionResult(marked, page_count, len(marked), compute_content_hash(marked), "complete")
    except (PdfExtractionError, OSError, ValueError) as error:
        return PdfExtractionResult(None, 0, 0, None, "failed", str(error))


def extract_pdf_text(payload: bytes, *, max_bytes: int, timeout_seconds: float = 60.0) -> str:
    """### Extract PDF text for compatibility callers, raising on unsuccessful results."""
    result = extract_pdf(payload, max_bytes=max_bytes, timeout_seconds=timeout_seconds)
    if result.extracted_text is None:
        raise PdfExtractionError(result.error_message or "pdf extraction failed")
    return result.extracted_text


def _run_bounded_extraction(
    payload: bytes,
    *,
    timeout_seconds: float,
    extractor: Callable[[bytes], str | tuple[str, int]],
) -> str | tuple[str, int]:
    """### Run extraction in a process that can be terminated on timeout."""
    context = get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_extract_worker, args=(sender, payload, extractor), daemon=True)
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout_seconds):
            process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join()
            raise PdfExtractionError(f"pdf extraction timed out after {timeout_seconds}s")
        succeeded, result = receiver.recv()
        process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join()
        if not succeeded:
            raise PdfExtractionError(f"pdf could not be parsed: {result}")
        if not isinstance(result, (str, tuple)):
            raise PdfExtractionError("pdf extraction worker returned an invalid result")
        return result
    except (EOFError, OSError) as error:
        process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join()
        raise PdfExtractionError("pdf extraction worker exited without a result") from error
    finally:
        receiver.close()


def _extract_worker(
    connection: Connection,
    payload: bytes,
    extractor: Callable[[bytes], str | tuple[str, int]],
) -> None:
    """### Send an extraction result to the parent process without sharing state."""
    try:
        connection.send((True, extractor(payload)))
    except Exception as error:  # $ Child-process failures return a simple serializable message.$
        connection.send((False, str(error)))
    finally:
        connection.close()


def _read_document_text(payload: bytes) -> tuple[str, int]:
    """### Read each page independently and retain page boundaries and count."""
    try:
        with pymupdf.open(stream=payload, filetype="pdf") as document:
            pages = [page.get_text("text") for page in document]
            page_count = document.page_count
    except Exception as error:  # $ Any parser failure surfaces as one typed extraction error.$
        raise PdfExtractionError(f"pdf could not be parsed: {error}") from error
    if not any(page.strip() for page in pages):
        raise PdfExtractionError("pdf contains no extractable text")
    return "\f".join(pages), page_count
