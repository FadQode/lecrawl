"""### Authoritative provider-credit accounting from run records.

Actual usage comes from `crawl_run_sources`; reserved usage comes from active runs'
`reserved_credits`. Committed = actual + reserved, so concurrent runs cannot both pass
the budget check.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.db.models import CrawlRun, CrawlRunSource

ACTIVE_STATUSES = ("running",)


def month_bounds(period: str) -> tuple[datetime, datetime]:
    """### Return [start, end) UTC datetimes for a `YYYY-MM` period."""
    year, month = (int(part) for part in period.split("-", 1))
    start = datetime(year, month, 1, tzinfo=UTC)
    end = datetime(year + 1, 1, 1, tzinfo=UTC) if month == 12 else datetime(year, month + 1, 1, tzinfo=UTC)
    return start, end


class CreditAccountingService:
    """### Compute actual, reserved, committed, and remaining credits per month."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        """### Store the session factory."""
        self._factory = session_factory

    def actual(self, period: str) -> int:
        """### Sum credits actually consumed in the period."""
        start, end = month_bounds(period)
        with self._factory() as session:
            total = session.scalar(
                select(func.coalesce(func.sum(CrawlRunSource.credits_used), 0))
                .join(CrawlRun, CrawlRun.id == CrawlRunSource.crawl_run_id)
                .where(CrawlRun.started_at >= start, CrawlRun.started_at < end)
            )
        return int(total or 0)

    def reserved(self, period: str) -> int:
        """### Sum credits reserved by active runs in the period."""
        start, end = month_bounds(period)
        with self._factory() as session:
            total = session.scalar(
                select(func.coalesce(func.sum(CrawlRun.reserved_credits), 0)).where(
                    CrawlRun.started_at >= start,
                    CrawlRun.started_at < end,
                    CrawlRun.status.in_(ACTIVE_STATUSES),
                )
            )
        return int(total or 0)

    def committed(self, period: str) -> int:
        """### Actual plus reserved credits for the period."""
        return self.actual(period) + self.reserved(period)

    def remaining(self, period: str, cap: int) -> int:
        """### Remaining budget for the period (never negative)."""
        return max(0, cap - self.committed(period))
