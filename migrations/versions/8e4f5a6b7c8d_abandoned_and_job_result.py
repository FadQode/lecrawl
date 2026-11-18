"""### abandoned run status + job result_status

Revision ID: 8e4f5a6b7c8d
Revises: 7d3e4f5a6b7c
Create Date: 2026-11-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e4f5a6b7c8d"
down_revision: str | None = "7d3e4f5a6b7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_status_check() -> None:
    op.execute(
        """
        DO $$
        DECLARE c record;
        BEGIN
          FOR c IN
            SELECT conname FROM pg_constraint
            WHERE conrelid = 'crawl_runs'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) ILIKE '%status%'
          LOOP
            EXECUTE format('ALTER TABLE crawl_runs DROP CONSTRAINT %I', c.conname);
          END LOOP;
        END $$;
        """
    )


def upgrade() -> None:
    """### Allow the 'abandoned' run status and add jobs.result_status."""
    _drop_status_check()
    op.execute(
        "ALTER TABLE crawl_runs ADD CONSTRAINT ck_crawl_runs_status_values CHECK "
        "(status IN ('running', 'success', 'partial_failure', 'failed', 'abandoned'))"
    )
    op.add_column("jobs", sa.Column("result_status", sa.String(length=24), nullable=True))


def downgrade() -> None:
    """### Revert the migration."""
    op.drop_column("jobs", "result_status")
    _drop_status_check()
    op.execute(
        "ALTER TABLE crawl_runs ADD CONSTRAINT ck_crawl_runs_status_values CHECK "
        "(status IN ('running', 'success', 'partial_failure', 'failed'))"
    )
