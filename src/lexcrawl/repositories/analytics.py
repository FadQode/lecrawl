"""### Read-only analytics over the observability views."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session


def pending_documents(session: Session, *, limit: int = 100) -> Sequence[RowMapping]:
    """### Return documents eligible for another processing attempt (bounded 1..500)."""
    bounded = max(1, min(limit, 500))
    statement = text(
        "SELECT source, document_id, identity_key, source_url, status, attempts, last_error, last_seen_at "
        "FROM v_pending_documents ORDER BY last_seen_at DESC LIMIT :limit"
    )
    return session.execute(statement, {"limit": bounded}).mappings().all()


def monthly_credit_usage(session: Session) -> Sequence[RowMapping]:
    """### Return monthly credits per source, derived from run records."""
    statement = text(
        "SELECT month, source, credits_used, executions, avg_credits_per_execution "
        "FROM v_monthly_credit_usage ORDER BY month DESC, source"
    )
    return session.execute(statement).mappings().all()
