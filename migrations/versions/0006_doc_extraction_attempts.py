"""### Track bounded detail-extraction attempts per document.

Revision ID: 0006_doc_extraction_attempts
Revises: 0005_run_preflight_state
Create Date: 2026-11-03
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_doc_extraction_attempts"
down_revision: str | None = "0005_run_preflight_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """### Add the attempt counter used by the bounded incomplete-report retry."""
    op.add_column(
        "documents",
        sa.Column("extraction_attempts", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.create_check_constraint("extraction_attempts_non_negative", "documents", "extraction_attempts >= 0")


def downgrade() -> None:
    """### Drop the attempt counter."""
    op.drop_constraint("extraction_attempts_non_negative", "documents", type_="check")
    op.drop_column("documents", "extraction_attempts")
