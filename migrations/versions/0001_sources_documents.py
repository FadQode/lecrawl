"""### Create the source registry and logical documents.

Revision ID: 0001_sources_documents
Revises:
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_sources_documents"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PRECISION_RULES = (
    "(published_precision = 'day' AND published_at IS NOT NULL) OR "
    "(published_precision = 'month' AND published_month IS NOT NULL AND published_year IS NOT NULL "
    "AND published_at IS NULL) OR "
    "(published_precision = 'year' AND published_year IS NOT NULL AND published_month IS NULL "
    "AND published_at IS NULL) OR "
    "(published_precision = 'unknown' AND published_at IS NULL AND published_month IS NULL "
    "AND published_year IS NULL)"
)


def upgrade() -> None:
    """### Create `sources` and `documents` with their constraints and indexes."""
    op.create_table(
        "sources",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_sources_code"),
    )

    op.create_table(
        "documents",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "source_id",
            sa.BigInteger(),
            sa.ForeignKey("sources.id", name="fk_documents_source_id_sources", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("identity_key", sa.Text(), nullable=False),
        sa.Column("native_id", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("listing_url", sa.Text(), nullable=True),
        sa.Column("document_type", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.Date(), nullable=True),
        sa.Column("published_year", sa.SmallInteger(), nullable=True),
        sa.Column("published_month", sa.SmallInteger(), nullable=True),
        sa.Column("published_precision", sa.String(length=8), nullable=False, server_default=sa.text("'unknown'")),
        sa.Column("published_date_raw", sa.Text(), nullable=True),
        sa.Column("topics", postgresql.ARRAY(sa.Text()), nullable=False, server_default=sa.text("'{}'::text[]")),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provenance", sa.String(length=16), nullable=False, server_default=sa.text("'live'")),
        sa.Column("archive_url", sa.Text(), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archive_listing_url", sa.Text(), nullable=True),
        sa.Column("extraction_status", sa.String(length=24), nullable=True),
        sa.Column("extraction_error", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "identity_key", name="uq_documents_source_id_identity_key"),
        sa.CheckConstraint(
            "published_precision IN ('day', 'month', 'year', 'unknown')",
            name="published_precision_values",
        ),
        sa.CheckConstraint(PRECISION_RULES, name="publication_precision_rules"),
        sa.CheckConstraint(
            "published_month IS NULL OR (published_month BETWEEN 1 AND 12 AND published_year IS NOT NULL)",
            name="published_month_range",
        ),
        sa.CheckConstraint(
            "published_year IS NULL OR published_year BETWEEN 1800 AND 2100",
            name="published_year_range",
        ),
        sa.CheckConstraint("provenance IN ('live', 'wayback')", name="provenance_values"),
        sa.CheckConstraint(
            "extraction_status IS NULL OR extraction_status IN "
            "('pending', 'complete', 'failed', 'access_blocked', 'unavailable')",
            name="extraction_status_values",
        ),
    )
    op.create_index("ix_documents_source_id_published_at", "documents", ["source_id", sa.text("published_at DESC")])
    op.create_index("ix_documents_source_id_last_seen_at", "documents", ["source_id", sa.text("last_seen_at DESC")])
    op.create_index("ix_documents_source_id_document_type", "documents", ["source_id", "document_type"])


def downgrade() -> None:
    """### Drop `documents` then `sources`."""
    op.drop_index("ix_documents_source_id_document_type", table_name="documents")
    op.drop_index("ix_documents_source_id_last_seen_at", table_name="documents")
    op.drop_index("ix_documents_source_id_published_at", table_name="documents")
    op.drop_table("documents")
    op.drop_table("sources")
