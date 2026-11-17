"""### pre-api hardening: keyset indexes, reservations, jobs

Revision ID: 5b2c9a8e7f10
Revises: 3c8d0e2b5f21
Create Date: 2026-11-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5b2c9a8e7f10"
down_revision: str | None = "3c8d0e2b5f21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """### Apply the migration."""
    op.execute(
        "CREATE INDEX ix_documents_source_published_id ON documents "
        "(source_id, published_at DESC NULLS LAST, id DESC)"
    )
    op.execute(
        "CREATE INDEX ix_documents_published_id ON documents (published_at DESC NULLS LAST, id DESC)"
    )
    op.execute("CREATE INDEX ix_documents_topics ON documents USING GIN (topics)")
    op.add_column(
        "crawl_runs",
        sa.Column("reserved_credits", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_type", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'queued'"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'claimed', 'running', 'succeeded', 'failed', 'rejected')",
            name=op.f("ck_jobs_status_values"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
        sa.UniqueConstraint("run_id", name=op.f("uq_jobs_run_id")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_jobs_idempotency_key")),
    )
    op.create_index("ix_jobs_status_created_at", "jobs", ["status", "created_at"])


def downgrade() -> None:
    """### Revert the migration."""
    op.drop_index("ix_jobs_status_created_at", table_name="jobs")
    op.drop_table("jobs")
    op.drop_column("crawl_runs", "reserved_credits")
    op.execute("DROP INDEX IF EXISTS ix_documents_topics")
    op.execute("DROP INDEX IF EXISTS ix_documents_published_id")
    op.execute("DROP INDEX IF EXISTS ix_documents_source_published_id")
