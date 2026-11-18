"""### phase 4.2: one lock and one active job per source

Revision ID: b4d5e6f7a8b9
Revises: 9a1c2b3d4e5f
Create Date: 2026-11-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4d5e6f7a8b9"
down_revision: str | None = "9a1c2b3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# $ A job is active while it is queued, claimed, or running for its source.$
ACTIVE_PREDICATE = "status IN ('queued', 'claimed', 'running')"


def upgrade() -> None:
    """### Add jobs.source and scope the active-job uniqueness to each source."""
    op.add_column("jobs", sa.Column("source", sa.String(length=32), nullable=True))
    # $ Backfill the source for legacy single-source jobs so the new index can apply.$
    op.execute(
        "UPDATE jobs SET source = payload->'sources'->>0 "
        "WHERE jsonb_array_length(COALESCE(payload->'sources', '[]'::jsonb)) = 1"
    )
    op.drop_index("uq_jobs_single_active", table_name="jobs")
    op.drop_constraint("uq_jobs_idempotency_key", "jobs", type_="unique")
    op.create_unique_constraint("uq_jobs_idempotency_key_source", "jobs", ["idempotency_key", "source"])
    op.create_index(
        "uq_jobs_active_per_source",
        "jobs",
        ["source"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_PREDICATE),
    )


def downgrade() -> None:
    """### Revert the migration."""
    op.drop_index("uq_jobs_active_per_source", table_name="jobs")
    op.drop_constraint("uq_jobs_idempotency_key_source", "jobs", type_="unique")
    op.create_unique_constraint("uq_jobs_idempotency_key", "jobs", ["idempotency_key"])
    op.create_index(
        "uq_jobs_single_active",
        "jobs",
        [sa.text("(1)")],
        unique=True,
        postgresql_where=sa.text(ACTIVE_PREDICATE),
    )
    op.drop_column("jobs", "source")
