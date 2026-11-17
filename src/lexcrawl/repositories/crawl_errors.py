"""### Persist sanitized crawl errors."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from lexcrawl.db.models import CrawlError


def add_error(
    session: Session,
    *,
    crawl_run_source_id: int,
    stage: str,
    error_type: str,
    message: str,
    now: datetime,
    document_id: int | None = None,
) -> None:
    """### Record one sanitized error occurrence for a source run."""
    session.add(
        CrawlError(
            crawl_run_source_id=crawl_run_source_id,
            document_id=document_id,
            stage=stage[:16],
            error_type=error_type[:32],
            message=message[:2000],
            occurred_at=now,
        )
    )
