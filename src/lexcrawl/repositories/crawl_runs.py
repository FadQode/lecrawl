"""### Crawl-run records: start, per-source metrics, and finish."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from lexcrawl.db.models import CrawlRun, CrawlRunSource


def start_run(
    session: Session, *, trigger_type: str, now: datetime, run_id: uuid.UUID | None = None
) -> tuple[int, uuid.UUID]:
    """### Insert a running crawl-run row and return (internal id, public run_id)."""
    run = CrawlRun(
        run_id=run_id or uuid.uuid4(),
        trigger_type=trigger_type,
        status="running",
        started_at=now,
        credits_spent=0,
        run_metadata={},
    )
    session.add(run)
    session.flush()
    return run.id, run.run_id


def record_source(
    session: Session,
    *,
    crawl_run_id: int,
    source_id: int,
    check_outcome: str,
    status: str,
    discovered: int,
    inserted: int,
    updated: int,
    skipped: int,
    failed: int,
    resources: int,
    pending: int,
    credits: int,
    pagination_complete: bool,
    detail: str,
    started_at: datetime,
    finished_at: datetime,
) -> int:
    """### Record one source's outcome and metrics within a run; return its id."""
    row = CrawlRunSource(
        crawl_run_id=crawl_run_id,
        source_id=source_id,
        check_outcome=check_outcome,
        status=status,
        discovered_count=discovered,
        inserted_count=inserted,
        updated_count=updated,
        skipped_count=skipped,
        failed_count=failed,
        resource_count=resources,
        pending_count=pending,
        credits_used=credits,
        pagination_complete=pagination_complete,
        detail=detail[:2000] if detail else None,
        started_at=started_at,
        finished_at=finished_at,
    )
    session.add(row)
    session.flush()
    return row.id


def finish_run(
    session: Session, *, crawl_run_id: int, status: str, credits: int, duration_ms: int, now: datetime
) -> None:
    """### Mark a crawl run finished with its status, credits, and duration."""
    run = session.get(CrawlRun, crawl_run_id)
    if run is None:
        return
    run.status = status
    run.credits_spent = credits
    run.duration_ms = duration_ms
    run.finished_at = now
