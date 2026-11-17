"""### enforce a single active crawl job

Revision ID: 7d3e4f5a6b7c
Revises: 5b2c9a8e7f10
Create Date: 2026-11-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "7d3e4f5a6b7c"
down_revision: str | None = "5b2c9a8e7f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """### Add a partial unique index allowing one queued/active job at a time."""
    op.execute(
        "CREATE UNIQUE INDEX uq_jobs_single_active ON jobs ((1)) "
        "WHERE status IN ('queued', 'claimed', 'running')"
    )


def downgrade() -> None:
    """### Revert the migration."""
    op.execute("DROP INDEX IF EXISTS uq_jobs_single_active")
