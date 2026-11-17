"""### mark pre-existing source-run metrics as unknown

Revision ID: 3c8d0e2b5f21
Revises: 2b7f9c1a4d10
Create Date: 2026-11-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3c8d0e2b5f21"
down_revision: str | None = "2b7f9c1a4d10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """### Mark rows that predate accurate metrics as unknown rather than guessing."""
    op.add_column(
        "crawl_run_sources",
        sa.Column("metrics_known", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    # $ Every row present at this migration predates reliable counters; don't invent precision.$
    op.execute("UPDATE crawl_run_sources SET metrics_known = false, status = 'unknown'")


def downgrade() -> None:
    """### Revert the migration."""
    op.drop_column("crawl_run_sources", "metrics_known")
