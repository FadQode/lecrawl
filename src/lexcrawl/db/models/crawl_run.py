"""### Crawl-run execution records for auditing and analytics."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lexcrawl.db.base import Base


class CrawlRun(Base):
    """### One orchestration run (scheduled, manual, api, force, or bootstrap)."""

    __tablename__ = "crawl_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('scheduled', 'manual', 'force', 'bootstrap', 'api')", name="trigger_type_values"
        ),
        CheckConstraint(
            "status IN ('running', 'success', 'partial_failure', 'failed')", name="status_values"
        ),
        Index("ix_crawl_runs_started_at", sql_text("started_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=sql_text("'running'"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    credits_spent: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    reserved_credits: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    # $ 'metadata' is reserved on the declarative base, so the attribute is run_metadata.$
    run_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )


class CrawlRunSource(Base):
    """### One source's outcome and metrics within a crawl run."""

    __tablename__ = "crawl_run_sources"
    __table_args__ = (UniqueConstraint("crawl_run_id", "source_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    crawl_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("crawl_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False)
    check_outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=sql_text("'success'"))
    discovered_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    inserted_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    updated_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    resource_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    metrics_known: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    credits_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    pagination_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("false"))
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )


class CrawlError(Base):
    """### One sanitized error occurrence within a source run."""

    __tablename__ = "crawl_errors"
    __table_args__ = (Index("ix_crawl_errors_crawl_run_source_id", "crawl_run_source_id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    crawl_run_source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("crawl_run_sources.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    error_type: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
