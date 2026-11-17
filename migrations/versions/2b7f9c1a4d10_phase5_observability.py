"""### phase 5 observability: counters, errors, views

Revision ID: 2b7f9c1a4d10
Revises: 1e622473cd7c
Create Date: 2026-11-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "2b7f9c1a4d10"
down_revision: str | None = "1e622473cd7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """### Apply the migration."""
    # crawl_runs: trigger -> trigger_type, plus duration and metadata.
    op.execute(
        """
        DO $$
        DECLARE c record;
        BEGIN
          FOR c IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'crawl_runs'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) ILIKE '%trigger%'
          LOOP
            EXECUTE format('ALTER TABLE crawl_runs DROP CONSTRAINT %I', c.conname);
          END LOOP;
        END $$;
        """
    )
    op.alter_column("crawl_runs", "trigger", new_column_name="trigger_type")
    op.execute(
        "ALTER TABLE crawl_runs ADD CONSTRAINT ck_crawl_runs_trigger_type_values "
        "CHECK (trigger_type IN ('scheduled', 'manual', 'force', 'bootstrap', 'api'))"
    )
    op.add_column("crawl_runs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.add_column(
        "crawl_runs",
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )

    # crawl_run_sources: rename counters, drop 'documents', add metrics/status/timing.
    op.alter_column("crawl_run_sources", "skipped_known", new_column_name="skipped_count")
    op.alter_column("crawl_run_sources", "provider_credits_used", new_column_name="credits_used")
    op.alter_column("crawl_run_sources", "pending", new_column_name="pending_count")
    op.drop_column("crawl_run_sources", "documents")
    op.add_column("crawl_run_sources", sa.Column("status", sa.String(length=24), server_default=sa.text("'success'"), nullable=False))
    op.add_column("crawl_run_sources", sa.Column("discovered_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("crawl_run_sources", sa.Column("inserted_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("crawl_run_sources", sa.Column("updated_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("crawl_run_sources", sa.Column("failed_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("crawl_run_sources", sa.Column("resource_count", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("crawl_run_sources", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("crawl_run_sources", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))

    # crawl_errors.
    op.create_table(
        "crawl_errors",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("crawl_run_source_id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=True),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("error_type", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["crawl_run_source_id"], ["crawl_run_sources.id"], name=op.f("fk_crawl_errors_crawl_run_source_id_crawl_run_sources"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name=op.f("fk_crawl_errors_document_id_documents"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_crawl_errors")),
    )
    op.create_index("ix_crawl_errors_crawl_run_source_id", "crawl_errors", ["crawl_run_source_id"])

    # Analytics views.
    op.execute(
        """
        CREATE VIEW v_pending_documents AS
        SELECT s.code AS source, d.id AS document_id, d.identity_key, d.source_url,
               d.extraction_status AS status, d.extraction_attempts AS attempts,
               d.extraction_error AS last_error, d.last_seen_at
        FROM documents d JOIN sources s ON s.id = d.source_id
        WHERE d.extraction_status IN ('pending', 'failed')
        """
    )
    op.execute(
        """
        CREATE VIEW v_monthly_credit_usage AS
        SELECT date_trunc('month', r.started_at) AS month, s.code AS source,
               SUM(rs.credits_used) AS credits_used, COUNT(*) AS executions,
               AVG(rs.credits_used) AS avg_credits_per_execution
        FROM crawl_run_sources rs
        JOIN crawl_runs r ON r.id = rs.crawl_run_id
        JOIN sources s ON s.id = rs.source_id
        GROUP BY 1, 2
        """
    )


def downgrade() -> None:
    """### Revert the migration."""
    op.execute("DROP VIEW IF EXISTS v_monthly_credit_usage")
    op.execute("DROP VIEW IF EXISTS v_pending_documents")
    op.drop_index("ix_crawl_errors_crawl_run_source_id", table_name="crawl_errors")
    op.drop_table("crawl_errors")
    for column in ("started_at", "finished_at", "resource_count", "failed_count", "updated_count", "inserted_count", "discovered_count", "status"):
        op.drop_column("crawl_run_sources", column)
    op.add_column("crawl_run_sources", sa.Column("documents", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.alter_column("crawl_run_sources", "pending_count", new_column_name="pending")
    op.alter_column("crawl_run_sources", "credits_used", new_column_name="provider_credits_used")
    op.alter_column("crawl_run_sources", "skipped_count", new_column_name="skipped_known")
    op.drop_column("crawl_runs", "metadata")
    op.drop_column("crawl_runs", "duration_ms")
    op.execute(
        """
        DO $$
        DECLARE c record;
        BEGIN
          FOR c IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'crawl_runs'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) ILIKE '%trigger%'
          LOOP
            EXECUTE format('ALTER TABLE crawl_runs DROP CONSTRAINT %I', c.conname);
          END LOOP;
        END $$;
        """
    )
    op.alter_column("crawl_runs", "trigger_type", new_column_name="trigger")
    op.execute(
        "ALTER TABLE crawl_runs ADD CONSTRAINT ck_crawl_runs_trigger_values "
        "CHECK (trigger IN ('scheduled', 'manual', 'force', 'bootstrap', 'api'))"
    )
