"""### Add preflight outcome and budget accounting to source runs.

Revision ID: 0005_run_preflight_state
Revises: 0004_crawl_errors
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_run_preflight_state"
down_revision: str | None = "0004_crawl_errors"
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
    "documents_pending",
    "provider_credits_used",
)
COUNTERS_NON_NEGATIVE = " AND ".join(f"{column} >= 0" for column in COUNTERS)


def upgrade() -> None:
    """### Add the preflight outcome and budget counters."""
    op.add_column(
        "crawl_run_sources",
        sa.Column("documents_pending", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "crawl_run_sources",
        sa.Column("provider_credits_used", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column("crawl_run_sources", sa.Column("check_outcome", sa.String(length=16), nullable=True))
    # $ The counter check is rebuilt so the new counters are covered by the same invariant.$
    op.drop_constraint("counters_non_negative", "crawl_run_sources", type_="check")
    op.create_check_constraint("counters_non_negative", "crawl_run_sources", COUNTERS_NON_NEGATIVE)
    op.create_check_constraint(
        "check_outcome_values",
        "crawl_run_sources",
        "check_outcome IS NULL OR check_outcome IN ('no_change', 'new_data', 'check_failed')",
    )


def downgrade() -> None:
    """### Remove the preflight outcome and budget counters."""
    op.drop_constraint("check_outcome_values", "crawl_run_sources", type_="check")
    op.drop_constraint("counters_non_negative", "crawl_run_sources", type_="check")
    op.create_check_constraint(
        "counters_non_negative",
        "crawl_run_sources",
        " AND ".join(
            f"{column} >= 0" for column in COUNTERS if column not in {"documents_pending", "provider_credits_used"}
        ),
    )
    op.drop_column("crawl_run_sources", "check_outcome")
    op.drop_column("crawl_run_sources", "provider_credits_used")
    op.drop_column("crawl_run_sources", "documents_pending")
