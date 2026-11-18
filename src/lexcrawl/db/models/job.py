"""### Durable crawl-job queue (PostgreSQL-backed)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lexcrawl.db.base import Base


class Job(Base):
    """### One queued crawl job, claimed by a worker."""

    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", "source"),
        CheckConstraint(
            "status IN ('queued', 'claimed', 'running', 'succeeded', 'failed', 'rejected')",
            name="status_values",
        ),
        Index("ix_jobs_status_created_at", "status", "created_at"),
        Index(
            "uq_jobs_active_per_source",
            "source",
            unique=True,
            postgresql_where=sql_text("status IN ('queued', 'claimed', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(24), nullable=False)
    # $ One job per source; NULL marks a legacy whole-run job from before per-source fan-out.$
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=sql_text("'queued'"))
    result_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'{}'::jsonb"))
