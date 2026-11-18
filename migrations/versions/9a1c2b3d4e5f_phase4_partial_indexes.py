"""### phase 4.8: partial indexes for due documents and claimable pdfs

Revision ID: 9a1c2b3d4e5f
Revises: 8e4f5a6b7c8d
Create Date: 2026-11-17
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "9a1c2b3d4e5f"
down_revision: str | None = "8e4f5a6b7c8d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """### Add partial indexes that keep the due-count and claim queries fast."""
    op.execute(
        "CREATE INDEX ix_documents_due ON documents (source_id) "
        "WHERE extraction_status IN ('pending', 'failed')"
    )
    op.execute(
        "CREATE INDEX ix_document_resources_claimed ON document_resources (claimed_at) "
        "WHERE kind = 'pdf' AND status IN ('pending', 'failed')"
    )


def downgrade() -> None:
    """### Revert the migration."""
    op.execute("DROP INDEX IF EXISTS ix_document_resources_claimed")
    op.execute("DROP INDEX IF EXISTS ix_documents_due")
