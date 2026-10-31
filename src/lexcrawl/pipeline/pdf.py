"""### PDF text extraction for resources linked from a source page."""

from __future__ import annotations

from collections.abc import Callable
from multiprocessing import get_context
from multiprocessing.connection import Connection
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

    return _run_bounded_extraction(payload, timeout_seconds=timeout_seconds, extractor=_read_document_text)


def _run_bounded_extraction(
    payload: bytes,
    *,
    timeout_seconds: float,
    extractor: Callable[[bytes], str],
) -> str:
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
        if not isinstance(result, str):
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


def _extract_worker(connection: Connection, payload: bytes, extractor: Callable[[bytes], str]) -> None:
    """### Send an extraction result to the parent process without sharing state."""
    try:
        connection.send((True, extractor(payload)))
    except Exception as error:  # $ Child-process failures return a simple serializable message.$
        connection.send((False, str(error)))
    finally:
        connection.close()


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
