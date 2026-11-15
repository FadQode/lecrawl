"""### Canonical document projection: one row per source + identity."""

from __future__ import annotations

from datetime import date, datetime
from typing import get_args

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
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
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from lexcrawl.db.base import Base
from lexcrawl.models.document import MAX_YEAR, MIN_YEAR, DocumentStatus, PublicationPrecision

PRECISION_VALUES = (*get_args(PublicationPrecision), "unknown")
STATUS_VALUES = get_args(DocumentStatus)

_PRECISION_RULES = (
    "(published_precision = 'day' AND published_at IS NOT NULL) OR "
    "(published_precision = 'month' AND published_month IS NOT NULL AND published_year IS NOT NULL "
    "AND published_at IS NULL) OR "
    "(published_precision = 'year' AND published_year IS NOT NULL AND published_month IS NULL "
    "AND published_at IS NULL) OR "
    "(published_precision = 'unknown' AND published_at IS NULL AND published_month IS NULL "
    "AND published_year IS NULL)"
)


def _in_list(values: tuple[str, ...]) -> str:
    """### Render a SQL IN-list literal from a tuple of allowed values."""
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


class Document(Base):
    """### One logical source document, keyed by its source-scoped identity."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("source_id", "identity_key"),
        CheckConstraint(f"published_precision IN {_in_list(PRECISION_VALUES)}", name="published_precision_values"),
        CheckConstraint(_PRECISION_RULES, name="publication_precision_rules"),
        CheckConstraint(
            "published_month IS NULL OR (published_month BETWEEN 1 AND 12 AND published_year IS NOT NULL)",
            name="published_month_range",
        ),
        CheckConstraint(
            f"published_year IS NULL OR published_year BETWEEN {MIN_YEAR} AND {MAX_YEAR}",
            name="published_year_range",
        ),
        CheckConstraint(
            f"extraction_status IS NULL OR extraction_status IN {_in_list(STATUS_VALUES)}",
            name="extraction_status_values",
        ),
        CheckConstraint("extraction_attempts >= 0", name="extraction_attempts_non_negative"),
        Index("ix_documents_source_id_published_at", "source_id", sql_text("published_at DESC")),
        Index(
            "ix_documents_source_id_extraction_status_extraction_attempts",
            "source_id",
            "extraction_status",
            "extraction_attempts",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False)
    identity_key: Mapped[str] = mapped_column(Text, nullable=False)
    native_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    published_month: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    published_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    published_date_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_precision: Mapped[str] = mapped_column(String(8), nullable=False, server_default=sql_text("'unknown'"))
    topics: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=sql_text("'{}'::text[]"))
    extraction_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=sql_text("0"))
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
