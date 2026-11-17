"""### Credit budget abstraction for the governor (DB authoritative, JSON for dev)."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine

from lexcrawl.checks.usage import UsageStore
from lexcrawl.db.session import create_session_factory
from lexcrawl.services.credit_accounting import CreditAccountingService


class CreditBudget(Protocol):
    """### Report committed credits and record new spend."""

    def committed(self, period: str) -> int:
        """### Return credits already committed (actual + reserved) for the period."""
        ...

    def record(self, period: str, credits: int) -> None:
        """### Record newly spent credits (JSON mode only; DB records via run rows)."""
        ...


class JsonCreditBudget:
    """### JSON-backed budget for tests and explicit local development."""

    def __init__(self, path: Path) -> None:
        """### Wrap a JSON usage store."""
        self._store = UsageStore(path)

    def committed(self, period: str) -> int:
        """### Return accumulated spend for the period."""
        return self._store.spent(period)

    def record(self, period: str, credits: int) -> None:
        """### Add spent credits to the JSON store."""
        self._store.add(period, credits)


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


def build_credit_budget(settings: object, *, engine: Engine | None = None) -> CreditBudget:
    """### Build the credit budget: DB-authoritative, or JSON for local development."""
    from lexcrawl.config import Settings

    if not isinstance(settings, Settings):
        raise TypeError("settings must be a Settings instance")
    if settings.state_backend == "json":
        return JsonCreditBudget(Path(settings.usage_state_path))
    if engine is None:
        raise ValueError("a database engine is required for the DB credit budget")
    return DbCreditBudget(engine)
