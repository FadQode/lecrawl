"""### Database queries for crawl-run history, per-source results, and errors."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from lexcrawl.db.models import CrawlError, CrawlRun, CrawlRunSource, Source


def list_runs(
    session: Session,
    *,
    source_code: str | None = None,
    status: str | None = None,
    limit: int,
    offset: int,
) -> tuple[list[CrawlRun], int]:
    """### Return one paginated page of crawl runs with their per-source results."""
    statement = select(CrawlRun).options(selectinload(CrawlRun.run_sources).selectinload(CrawlRunSource.source))
    count_statement = select(func.count()).select_from(CrawlRun)
    if status is not None:
        statement = statement.where(CrawlRun.status == status)
        count_statement = count_statement.where(CrawlRun.status == status)
    if source_code is not None:
        source_id = select(Source.id).where(Source.code == source_code).scalar_subquery()
        statement = statement.join(CrawlRunSource, CrawlRunSource.crawl_run_id == CrawlRun.id).where(
            CrawlRunSource.source_id == source_id
        )
        count_statement = count_statement.join(CrawlRunSource, CrawlRunSource.crawl_run_id == CrawlRun.id).where(
            CrawlRunSource.source_id == source_id
        )
    total = int(session.execute(count_statement).scalar_one())
    ordered = statement.order_by(CrawlRun.started_at.desc(), CrawlRun.id.desc()).limit(limit).offset(offset)
    return list(session.execute(ordered).scalars()), total


def get_run(session: Session, run_id: UUID) -> CrawlRun | None:
    """### Return one crawl run with its per-source results, or None."""
    statement = (
        select(CrawlRun)
        .where(CrawlRun.run_id == run_id)
        .options(selectinload(CrawlRun.run_sources).selectinload(CrawlRunSource.source))
    )
    return session.execute(statement).scalar_one_or_none()


def list_errors(
    session: Session,
    run_id: UUID,
    *,
    stage: str | None = None,
    error_type: str | None = None,
    limit: int,
    offset: int,
) -> tuple[list[CrawlError], int]:
    """### Return one paginated page of structured errors for a crawl run."""
    base: Select[tuple[CrawlError]] = (
        select(CrawlError)
        .join(CrawlRunSource, CrawlError.crawl_run_source_id == CrawlRunSource.id)
        .join(CrawlRun, CrawlRunSource.crawl_run_id == CrawlRun.id)
        .where(CrawlRun.run_id == run_id)
    )
    if stage is not None:
        base = base.where(CrawlError.stage == stage)
    if error_type is not None:
        base = base.where(CrawlError.error_type == error_type)
    total = int(session.execute(select(func.count()).select_from(base.subquery())).scalar_one())
    ordered = base.order_by(CrawlError.occurred_at.desc(), CrawlError.id.desc()).limit(limit).offset(offset)
    return list(session.execute(ordered).scalars()), total
