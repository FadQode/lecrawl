"""### Transactional persistence of actor results and whole crawl reports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.actors.base import ActorRunResult
from lexcrawl.db.models import CrawlRun
from lexcrawl.db.seed import source_id_for
from lexcrawl.db.session import session_scope
from lexcrawl.logging import get_logger
from lexcrawl.models.document import CanonicalDocument
from lexcrawl.orchestrator import CrawlRunReport
from lexcrawl.repositories.crawl_runs import (
    AttributedError,
    create_run,
    finalize_run,
    replace_errors,
    upsert_run_source,
)
from lexcrawl.repositories.documents import upsert_document
from lexcrawl.repositories.localizations import upsert_localization
from lexcrawl.repositories.mapping import (
    DOCUMENT_ERROR_TYPE,
    EXTRACTION_STAGE,
    RESOURCE_ERROR_TYPE,
    TRANSLATION_ERROR_TYPE,
    ErrorPayload,
    error_summary,
    localization_payloads,
    redact_secrets,
    run_counters,
    run_error_payloads,
)
from lexcrawl.repositories.resources import upsert_resource

LOGGER = get_logger(__name__)

DEFAULT_TRIGGER = "manual"


class PersistenceError(RuntimeError):
    """### Raised when a run cannot be persisted."""


@dataclass(frozen=True, slots=True)
class PersistedSourceRun:
    """### Outcome of persisting one actor result."""

    crawl_run_id: int
    source_code: str
    status: str
    documents: int
    errors: int


@dataclass(frozen=True, slots=True)
class PersistedCrawlRun:
    """### Outcome of persisting a whole crawl report."""

    run_id: UUID
    status: str
    sources: tuple[PersistedSourceRun, ...]


def persist_source_result(
    session: Session,
    *,
    crawl_run_id: int,
    result: ActorRunResult,
    now: datetime | None = None,
) -> PersistedSourceRun:
    """### Persist one actor result inside the caller's transaction."""
    timestamp = now or datetime.now(tz=UTC)
    source_id = source_id_for(session, result.source)
    if source_id is None:
        raise PersistenceError(f"source {result.source!r} is not registered; run `lexcrawl db seed` before persisting")

    run_source_id = upsert_run_source(
        session,
        crawl_run_id=crawl_run_id,
        source_id=source_id,
        status=result.status,
        counters=run_counters(result),
        pagination_complete=result.pagination_complete,
        error_summary=redact_secrets(error_summary(result) or ""),
        started_at=timestamp - timedelta(seconds=max(0.0, result.duration_seconds)),
        finished_at=timestamp,
        check_outcome=result.check_outcome,
    )

    errors: list[AttributedError] = [AttributedError(payload=payload) for payload in run_error_payloads(result)]
    for document in result.documents:
        errors.extend(_persist_document(session, source_id=source_id, document=document, now=timestamp))

    replace_errors(session, crawl_run_source_id=run_source_id, errors=errors, occurred_at=timestamp)
    return PersistedSourceRun(
        crawl_run_id=crawl_run_id,
        source_code=result.source,
        status=result.status,
        documents=len(result.documents),
        errors=len(errors),
    )


def _persist_document(
    session: Session,
    *,
    source_id: int,
    document: CanonicalDocument,
    now: datetime,
) -> list[AttributedError]:
    """### Persist one document with its localizations and resources."""
    document_id = upsert_document(session, source_id=source_id, document=document, now=now)
    errors: list[AttributedError] = []

    for index, payload in enumerate(localization_payloads(document)):
        localization_id = upsert_localization(session, document_id=document_id, payload=payload, now=now)
        if index > 0 and payload.error:
            # $ The primary error is recorded on the document; translations keep their own.
            errors.append(
                AttributedError(
                    payload=ErrorPayload(
                        stage="unknown",
                        error_type=TRANSLATION_ERROR_TYPE,
                        message=redact_secrets(payload.error),
                    ),
                    localization_id=localization_id,
                )
            )

    if document.extraction_error:
        errors.append(
            AttributedError(
                payload=ErrorPayload(
                    stage="unknown",
                    error_type=DOCUMENT_ERROR_TYPE,
                    message=redact_secrets(document.extraction_error),
                ),
                document_id=document_id,
            )
        )

    for resource in document.resources:
        resource_id = upsert_resource(session, document_id=document_id, resource=resource, now=now)
        if resource.error:
            errors.append(
                AttributedError(
                    payload=ErrorPayload(
                        stage=EXTRACTION_STAGE,
                        error_type=RESOURCE_ERROR_TYPE,
                        message=redact_secrets(resource.error),
                    ),
                    resource_id=resource_id,
                )
            )
    return errors


def persist_failed_source(
    session: Session,
    *,
    crawl_run_id: int,
    result: ActorRunResult,
    error: Exception,
    now: datetime | None = None,
) -> PersistedSourceRun:
    """### Record a source whose persistence failed, without its rolled-back documents."""
    timestamp = now or datetime.now(tz=UTC)
    source_id = source_id_for(session, result.source)
    if source_id is None:
        raise PersistenceError(f"source {result.source!r} is not registered")
    counters = run_counters(result) | {"documents_processed": 0}
    message = redact_secrets(f"persistence failed: {error}")
    run_source_id = upsert_run_source(
        session,
        crawl_run_id=crawl_run_id,
        source_id=source_id,
        status="failed",
        counters=counters,
        pagination_complete=result.pagination_complete,
        error_summary=message,
        started_at=timestamp,
        finished_at=timestamp,
        check_outcome=result.check_outcome,
    )
    replace_errors(
        session,
        crawl_run_source_id=run_source_id,
        errors=(
            AttributedError(payload=ErrorPayload(stage="persistence", error_type="persistence_error", message=message)),
        ),
        occurred_at=timestamp,
    )
    return PersistedSourceRun(
        crawl_run_id=crawl_run_id,
        source_code=result.source,
        status="failed",
        documents=0,
        errors=1,
    )


def aggregate_status(outcomes: list[PersistedSourceRun]) -> str:
    """### Derive the run status from the persisted source outcomes."""
    if not outcomes:
        return "failed"
    statuses = {outcome.status for outcome in outcomes}
    if statuses == {"success"}:
        return "success"
    if "success" in statuses or "partial_failure" in statuses:
        return "partial_failure"
    return "failed"


def persist_crawl_report(
    session_factory: sessionmaker[Session],
    report: CrawlRunReport,
    *,
    trigger_type: str = DEFAULT_TRIGGER,
    started_at: datetime | None = None,
) -> PersistedCrawlRun:
    """### Persist one crawl report with one transaction per source actor."""
    started = started_at or datetime.now(tz=UTC)
    with session_scope(session_factory) as session:
        # $ An unregistered source has no registry row, so the run is refused before any write.$
        missing = [item.source for item in report.results if source_id_for(session, item.source) is None]
    if missing:
        raise PersistenceError(
            f"sources not registered: {sorted(set(missing))}; run `lexcrawl db seed` before persisting"
        )

    with session_scope(session_factory) as session:
        run = create_run(session, trigger_type=trigger_type, started_at=started)
        run_id = run.run_id
        run_pk = run.id

    outcomes: list[PersistedSourceRun] = []
    for result in report.results:
        try:
            with session_scope(session_factory) as session:
                outcomes.append(persist_source_result(session, crawl_run_id=run_pk, result=result))
        except Exception as error:  # $ One source's persistence failure must not roll back its siblings.$
            LOGGER.exception("source_persistence_failed", extra={"source": result.source})
            try:
                with session_scope(session_factory) as session:
                    outcomes.append(persist_failed_source(session, crawl_run_id=run_pk, result=result, error=error))
            except Exception:  # $ A run that cannot record a failure is itself failed and logged.$
                LOGGER.exception("source_failure_not_recorded", extra={"source": result.source})

    finished = datetime.now(tz=UTC)
    status = aggregate_status(outcomes)
    duration_ms = int((finished - started).total_seconds() * 1000)
    with session_scope(session_factory) as session:
        stored_run = session.get(CrawlRun, run_pk)
        if stored_run is None:
            raise PersistenceError(f"crawl run {run_id} disappeared before finalisation")
        finalize_run(session, run=stored_run, status=status, finished_at=finished, duration_ms=duration_ms)

    return PersistedCrawlRun(run_id=run_id, status=status, sources=tuple(outcomes))
