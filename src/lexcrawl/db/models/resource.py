"""### Linked resource (attachment) projection.

Each resource is extracted and retried independently; a failed attachment never
blocks its siblings or the parent document.
"""

from __future__ import annotations

from datetime import datetime
from typing import get_args

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column

from lexcrawl.db.base import Base
from lexcrawl.models.document import ResourceKind, ResourceStatus

KIND_VALUES = get_args(ResourceKind)
STATUS_VALUES = get_args(ResourceStatus)


def _in_list(values: tuple[str, ...]) -> str:
    """### Render a SQL IN-list literal from a tuple of allowed values."""
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


class DocumentResource(Base):
    """### One attachment belonging to a document."""

    __tablename__ = "document_resources"
    __table_args__ = (
        UniqueConstraint("document_id", "url"),
        CheckConstraint(f"kind IN {_in_list(KIND_VALUES)}", name="kind_values"),
        CheckConstraint(f"status IN {_in_list(STATUS_VALUES)}", name="status_values"),
        CheckConstraint(
            "status <> 'complete' OR (text IS NOT NULL AND content_hash IS NOT NULL)",
            name="complete_requires_text",
        ),
        CheckConstraint(
            "url IS NOT NULL OR status = 'url_unavailable'",
            name="url_required_unless_unavailable",
        ),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_document_resources_document_id", "document_id"),
        Index("ix_document_resources_status_attempts", "status", "attempts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    document_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, server_default=sql_text("'other'"))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default=sql_text("'pending'"))
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=sql_text("0"))
    last_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # $ A durable claim marker so workers on other hosts skip a row already being fetched.$
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
