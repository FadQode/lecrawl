"""### Create structured crawl errors.

Revision ID: 0004_crawl_errors
Revises: 0003_crawl_history
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_crawl_errors"
down_revision: str | None = "0003_crawl_history"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STAGES = "'discovery', 'fetch', 'parse', 'validate', 'extract', 'persistence', 'unknown'"


def upgrade() -> None:
    """### Create `crawl_errors` with attribution constraints and indexes."""
    op.create_table(
        "crawl_errors",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "crawl_run_source_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "crawl_run_sources.id", name="fk_crawl_errors_crawl_run_source_id_crawl_run_sources", ondelete="CASCADE"
            ),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            sa.BigInteger(),
            sa.ForeignKey("documents.id", name="fk_crawl_errors_document_id_documents", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "localization_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "document_localizations.id",
                name="fk_crawl_errors_localization_id_document_localizations",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column(
            "resource_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "document_resources.id", name="fk_crawl_errors_resource_id_document_resources", ondelete="SET NULL"
            ),
            nullable=True,
        ),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("error_type", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"stage IN ({STAGES})", name="stage_values"),
        sa.CheckConstraint(
            "num_nonnulls(document_id, localization_id, resource_id) <= 1",
            name="single_attribution",
        ),
    )
    op.create_index(
        "ix_crawl_errors_crawl_run_source_id_occurred_at",
        "crawl_errors",
        ["crawl_run_source_id", sa.text("occurred_at DESC")],
    )
    op.create_index(
        "ix_crawl_errors_error_type_occurred_at", "crawl_errors", ["error_type", sa.text("occurred_at DESC")]
    )


def downgrade() -> None:
    """### Drop `crawl_errors`."""
    op.drop_index("ix_crawl_errors_error_type_occurred_at", table_name="crawl_errors")
    op.drop_index("ix_crawl_errors_crawl_run_source_id_occurred_at", table_name="crawl_errors")
    op.drop_table("crawl_errors")
