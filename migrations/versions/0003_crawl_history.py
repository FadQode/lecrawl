"""### Create crawl run history.

Revision ID: 0003_crawl_history
Revises: 0002_document_content
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_crawl_history"
down_revision: str | None = "0002_document_content"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COUNTERS = (
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
)
COUNTERS_NON_NEGATIVE = " AND ".join(f"{column} >= 0" for column in COUNTERS)


def upgrade() -> None:
    """### Create `crawl_runs` and `crawl_run_sources`."""
    op.create_table(
        "crawl_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", name="uq_crawl_runs_run_id"),
        sa.CheckConstraint("trigger_type IN ('manual', 'scheduled')", name="trigger_type_values"),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'partial_failure', 'failed')", name="status_values"
        ),
        sa.CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_non_negative"),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="finished_after_started"
        ),
    )

    columns = [
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "crawl_run_id",
            sa.BigInteger(),
            sa.ForeignKey("crawl_runs.id", name="fk_crawl_run_sources_crawl_run_id_crawl_runs", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "source_id",
            sa.BigInteger(),
            sa.ForeignKey("sources.id", name="fk_crawl_run_sources_source_id_sources", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
    ]
    columns.extend(sa.Column(column, sa.Integer(), nullable=False, server_default=sa.text("0")) for column in COUNTERS)
    columns.extend(
        [
            sa.Column("pagination_complete", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("error_summary", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        ]
    )
    op.create_table(
        "crawl_run_sources",
        *columns,
        sa.UniqueConstraint("crawl_run_id", "source_id", name="uq_crawl_run_sources_crawl_run_id_source_id"),
        sa.CheckConstraint(
            "status IN ('success', 'partial_failure', 'failed')", name="status_values"
        ),
        sa.CheckConstraint(COUNTERS_NON_NEGATIVE, name="counters_non_negative"),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="finished_after_started"
        ),
    )

    op.create_index("ix_crawl_runs_started_at", "crawl_runs", [sa.text("started_at DESC")])
    op.create_index("ix_crawl_runs_status_started_at", "crawl_runs", ["status", sa.text("started_at DESC")])
    op.create_index(
        "ix_crawl_run_sources_source_id_started_at", "crawl_run_sources", ["source_id", sa.text("started_at DESC")]
    )
    op.create_index("ix_crawl_run_sources_crawl_run_id_status", "crawl_run_sources", ["crawl_run_id", "status"])


def downgrade() -> None:
    """### Drop `crawl_run_sources` then `crawl_runs`."""
    op.drop_index("ix_crawl_run_sources_crawl_run_id_status", table_name="crawl_run_sources")
    op.drop_index("ix_crawl_run_sources_source_id_started_at", table_name="crawl_run_sources")
    op.drop_index("ix_crawl_runs_status_started_at", table_name="crawl_runs")
    op.drop_index("ix_crawl_runs_started_at", table_name="crawl_runs")
    op.drop_table("crawl_run_sources")
    op.drop_table("crawl_runs")
