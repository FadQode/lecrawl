"""### Crawl run and per-source run entities."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal, get_args
from uuid import UUID

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
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lexcrawl.actors.base import RunStatus
from lexcrawl.db.base import Base
from lexcrawl.db.models.checks import sql_in, timestamp_order

if TYPE_CHECKING:
    from lexcrawl.db.models.crawl_error import CrawlError
    from lexcrawl.db.models.source import Source

TriggerType = Literal["manual", "scheduled", "api"]
TRIGGER_VALUES = get_args(TriggerType)
RunLifecycleStatus = Literal["running", "success", "partial_failure", "failed"]
RUN_STATUS_VALUES = get_args(RunLifecycleStatus)

# $ Every counter is non-negative and counts the unit named by its column.$
_COUNTER_COLUMNS = (
    "listing_pages_attempted",
    "listing_pages_succeeded",
    "listing_pages_failed",
    "articles_discovered",
    "article_urls_deduplicated",
    "records_filtered_out",
    "detail_pages_attempted",
    "detail_pages_succeeded",
    "detail_pages_failed",
    "records_missing_required_fields",
    "records_without_text",
    "resources_discovered",
    "resources_processed",
    "resources_extracted",
    "resources_unavailable",
    "resources_failed",
    "resources_skipped",
    "documents_processed",
    "documents_failed",
    "documents_pending",
    "provider_credits_used",
)
CHECK_OUTCOME_VALUES = ("no_change", "new_data", "check_failed")
_COUNTERS_NON_NEGATIVE = " AND ".join(f"{column} >= 0" for column in _COUNTER_COLUMNS)


class CrawlRun(Base):
    """### One overall crawler invocation."""

    __tablename__ = "crawl_runs"
    __table_args__ = (
        CheckConstraint(f"trigger_type {sql_in(TRIGGER_VALUES)}", name="trigger_type_values"),
        CheckConstraint(f"status {sql_in(RUN_STATUS_VALUES)}", name="status_values"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"),
        CheckConstraint(timestamp_order("started_at", "finished_at"), name="finished_after_started"),
        Index("ix_crawl_runs_started_at", text("started_at DESC")),
        Index("ix_crawl_runs_status_started_at", "status", text("started_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False, unique=True)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run_sources: Mapped[list[CrawlRunSource]] = relationship(
        back_populates="crawl_run", cascade="all, delete-orphan", passive_deletes=True
    )


class CrawlRunSource(Base):
    """### One source actor's outcome inside a crawl run."""

    __tablename__ = "crawl_run_sources"
    __table_args__ = (
        UniqueConstraint("crawl_run_id", "source_id"),
        CheckConstraint(f"status {sql_in(get_args(RunStatus))}", name="status_values"),
        CheckConstraint(
            f"check_outcome IS NULL OR check_outcome {sql_in(CHECK_OUTCOME_VALUES)}", name="check_outcome_values"
        ),
        CheckConstraint(_COUNTERS_NON_NEGATIVE, name="counters_non_negative"),
        CheckConstraint(timestamp_order("started_at", "finished_at"), name="finished_after_started"),
        Index("ix_crawl_run_sources_source_id_started_at", "source_id", text("started_at DESC")),
        Index("ix_crawl_run_sources_crawl_run_id_status", "crawl_run_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    crawl_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("crawl_runs.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    listing_pages_attempted: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    listing_pages_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    listing_pages_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    articles_discovered: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    article_urls_deduplicated: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_filtered_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    detail_pages_attempted: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    detail_pages_succeeded: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    detail_pages_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_missing_required_fields: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    records_without_text: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    resources_discovered: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    resources_processed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    resources_extracted: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    resources_unavailable: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    resources_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    resources_skipped: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    documents_processed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    documents_failed: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    documents_pending: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    provider_credits_used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    check_outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pagination_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    crawl_run: Mapped[CrawlRun] = relationship(back_populates="run_sources")
    source: Mapped[Source] = relationship(back_populates="run_sources")
    errors: Mapped[list[CrawlError]] = relationship(
        back_populates="run_source", cascade="all, delete-orphan", passive_deletes=True
    )
