"""### Credit budget for the governor, backed by PostgreSQL run records."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import Engine

from lexcrawl.db.session import create_session_factory
from lexcrawl.services.credit_accounting import CreditAccountingService


def current_period(now: datetime | None = None) -> str:
    """### Return the current UTC calendar month as a `YYYY-MM` key."""
    return (now or datetime.now(tz=UTC)).strftime("%Y-%m")


class CreditBudget(Protocol):
    """### Report committed credits and record new spend."""

    def committed(self, period: str) -> int:
        """### Return credits already committed (actual + reserved) for the period."""
        ...

    def record(self, period: str, credits: int) -> None:
        """### Record newly spent credits (DB records them via run rows)."""
        ...


class DbCreditBudget:
    """### PostgreSQL-authoritative budget (run rows are the accounting record)."""

    def __init__(self, engine: Engine) -> None:
        """### Build the accounting service over a shared engine."""
        self._accounting = CreditAccountingService(create_session_factory(engine))

    def committed(self, period: str) -> int:
        """### Return actual plus reserved credits for the period."""
        return self._accounting.committed(period)

    def record(self, period: str, credits: int) -> None:
        """### No-op: DB credits are recorded per run at finalization."""
        return None


def build_credit_budget(engine: Engine) -> CreditBudget:
    """### Build the DB-backed credit budget."""
    return DbCreditBudget(engine)
