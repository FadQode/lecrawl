"""### Alembic revision template."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "${up_revision}"
down_revision: str | None = "${down_revision}"
branch_labels: str | Sequence[str] | None = ${branch_labels}
depends_on: str | Sequence[str] | None = ${depends_on}


def upgrade() -> None:
    """### Apply this revision."""
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    """### Revert this revision."""
    ${downgrades if downgrades else "pass"}
