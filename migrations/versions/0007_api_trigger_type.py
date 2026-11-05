"""### Allow API-triggered crawl runs.

Revision ID: 0007_api_trigger_type
Revises: 0006_doc_extraction_attempts
Create Date: 2026-11-05
"""

from __future__ import annotations

from alembic import op

revision = "0007_api_trigger_type"
down_revision = "0006_doc_extraction_attempts"
branch_labels = None
depends_on = None

_TRIGGER_CONSTRAINT = "ck_crawl_runs_trigger_type_values"
_WITH_API = "trigger_type IN ('manual', 'scheduled', 'api')"
_WITHOUT_API = "trigger_type IN ('manual', 'scheduled')"


def upgrade() -> None:
    """### Widen the trigger_type check to include API-triggered runs."""
    op.drop_constraint(op.f(_TRIGGER_CONSTRAINT), "crawl_runs", type_="check")
    op.create_check_constraint(op.f(_TRIGGER_CONSTRAINT), "crawl_runs", _WITH_API)


def downgrade() -> None:
    """### Restore the original trigger_type check."""
    op.drop_constraint(op.f(_TRIGGER_CONSTRAINT), "crawl_runs", type_="check")
    op.create_check_constraint(op.f(_TRIGGER_CONSTRAINT), "crawl_runs", _WITHOUT_API)
