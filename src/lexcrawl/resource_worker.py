"""### Resource queue worker: extract due PDF attachments on their own schedule.

Crawl workers do not drive attachment extraction here; this worker claims due, retryable
PDF attachments from the database (honouring the claim lease) and runs PyMuPDF extraction
in a process pool so CPU-heavy work scales independently of crawling.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor

from sqlalchemy import Engine

from lexcrawl.config import Settings
from lexcrawl.db.engine import create_db_engine, database_configured
from lexcrawl.db.session import create_session_factory
from lexcrawl.factory import build_resource_fetcher
from lexcrawl.logging import get_logger
from lexcrawl.pipeline.pdf import extract_pdf_text
from lexcrawl.services.content_processing import (
    ContentProcessingService,
    ExtractResult,
    ResourceExtractor,
    extract_pdf_text_many,
)
from lexcrawl.services.fetchers import HttpResourceFetcher

LOGGER = get_logger(__name__)

DEFAULT_POLL_SECONDS = 5.0
DEFAULT_BATCH_SIZE = 10
DEFAULT_PROCESSES = 2


def _extract_one(payload: bytes) -> ExtractResult:
    """### Extract one PDF, capturing its error (must be picklable for the pool)."""
    try:
        return extract_pdf_text(payload), None
    except Exception as error:  # $ One bad PDF must not fail its batch.$
        return None, str(error)


def _pool_extractor(executor: ProcessPoolExecutor) -> ResourceExtractor:
    """### Return a batch extractor that maps every payload across the process pool."""

    def extract(payloads: Sequence[bytes]) -> Sequence[ExtractResult]:
        """### Submit the whole batch, then collect the results in order."""
        return list(executor.map(_extract_one, payloads))

    return extract


def _build_service(
    settings: Settings,
    engine: Engine,
    fetcher: HttpResourceFetcher,
    *,
    extract: ResourceExtractor,
) -> ContentProcessingService:
    """### Build a content-processing service wired to a resource fetcher and extractor."""
    return ContentProcessingService(
        create_session_factory(engine),
        fetcher=fetcher,
        attempt_cap=settings.resource_extraction_attempts,
        max_bytes=settings.resource_max_bytes,
        lease_seconds=settings.resource_claim_lease_seconds,
        extract=extract,
    )


def process_resources_once(
    settings: Settings,
    *,
    engine: Engine | None = None,
    limit: int = DEFAULT_BATCH_SIZE,
    extract: ResourceExtractor = extract_pdf_text_many,
    fetcher: HttpResourceFetcher | None = None,
) -> int:
    """### Claim and process one batch of due attachments; return how many were handled."""
    owns_engine = engine is None
    engine = engine or create_db_engine(settings)
    resource_fetcher = fetcher or HttpResourceFetcher(build_resource_fetcher(settings))
    try:
        service = _build_service(settings, engine, resource_fetcher, extract=extract)
        summary = service.process_due_resources(limit=limit)
        return summary.extracted + summary.failed
    finally:
        if fetcher is None:
            resource_fetcher.close()
        if owns_engine:
            engine.dispose()


def run_resources_forever(
    settings: Settings,
    *,
    interval_seconds: float = DEFAULT_POLL_SECONDS,
    limit: int = DEFAULT_BATCH_SIZE,
    processes: int = DEFAULT_PROCESSES,
    stop: threading.Event | None = None,
) -> None:
    """### Poll the resource queue until stopped, extracting PDFs in a process pool."""
    if not database_configured(settings):
        raise ValueError("DATABASE_URL is required for the resource worker")
    engine = create_db_engine(settings)
    try:
        with ProcessPoolExecutor(max_workers=max(1, processes)) as executor:
            extract = _pool_extractor(executor)
            while stop is None or not stop.is_set():
                handled = process_resources_once(settings, engine=engine, limit=limit, extract=extract)
                if handled == 0:
                    _pause(stop, interval_seconds)
    finally:
        engine.dispose()


def _pause(stop: threading.Event | None, seconds: float) -> None:
    """### Wait between polls, waking early when the stop flag is set."""
    if stop is not None:
        stop.wait(seconds)
    else:
        time.sleep(seconds)
