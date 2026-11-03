"""### Crawl run repository: run lifecycle, per-source outcomes, and errors."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lexcrawl.db.models import CrawlError, CrawlRun, CrawlRunSource
from lexcrawl.repositories.mapping import ErrorPayload


@dataclass(frozen=True, slots=True)
class AttributedError:
    """### One error row with optional record attribution."""

    payload: ErrorPayload
    document_id: int | None = None
    localization_id: int | None = None
    resource_id: int | None = None


def create_run(
    session: Session,
    *,
    trigger_type: str,
    started_at: datetime,
    run_id: UUID | None = None,
) -> CrawlRun:
    """### Insert the run row that later source outcomes attach to."""
    run = CrawlRun(
        run_id=run_id or uuid4(),
        trigger_type=trigger_type,
        status="running",
        started_at=started_at,
        created_at=started_at,
    )
    session.add(run)
    session.flush()
    return run


def upsert_run_source(
    session: Session,
    *,
    crawl_run_id: int,
    source_id: int,
    status: str,
    counters: dict[str, int],
    pagination_complete: bool,
    error_summary: str | None,
    started_at: datetime,
    finished_at: datetime,
) -> int:
    """### Insert or replace one source outcome inside a run."""
    values: dict[str, object] = {
        "crawl_run_id": crawl_run_id,
        "source_id": source_id,
        "status": status,
        "pagination_complete": pagination_complete,
        "error_summary": error_summary,
        "started_at": started_at,
        "finished_at": finished_at,
        **counters,
    }
    mutable = {
        "status": status,
        "pagination_complete": pagination_complete,
        "error_summary": error_summary,
        "started_at": started_at,
        "finished_at": finished_at,
        **counters,
    }
    insert_statement = insert(CrawlRunSource).values(**values)
    statement = insert_statement.on_conflict_do_update(
        index_elements=[CrawlRunSource.crawl_run_id, CrawlRunSource.source_id],
        set_=mutable,
    ).returning(CrawlRunSource.id)
    return int(session.execute(statement).scalar_one())


def replace_errors(
    session: Session,
    *,
    crawl_run_source_id: int,
    errors: Sequence[AttributedError],
    occurred_at: datetime,
) -> int:
    """### Replace the error rows of one source run, keeping re-persistence idempotent."""
    session.execute(delete(CrawlError).where(CrawlError.crawl_run_source_id == crawl_run_source_id))
    if not errors:
        return 0
    session.execute(
        insert(CrawlError).values(
            [
                {
                    "crawl_run_source_id": crawl_run_source_id,
                    "document_id": error.document_id,
                    "localization_id": error.localization_id,
                    "resource_id": error.resource_id,
                    "stage": error.payload.stage,
                    "error_type": error.payload.error_type,
                    "message": error.payload.message,
                    "retryable": error.payload.retryable,
                    "occurred_at": occurred_at,
                }
                for error in errors
            ]
        )
    )
    return len(errors)


def finalize_run(
    session: Session,
    *,
    run: CrawlRun,
    status: str,
    finished_at: datetime,
    duration_ms: int,
) -> None:
    """### Record the final run status and timing."""
    run.status = status
    run.finished_at = finished_at
    run.duration_ms = max(0, duration_ms)
