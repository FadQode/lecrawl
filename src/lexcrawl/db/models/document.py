"""### Logical document identity entity."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, get_args

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
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lexcrawl.db.base import Base
from lexcrawl.db.models.checks import sql_in
from lexcrawl.models.document import DocumentStatus, Provenance, PublicationPrecision

if TYPE_CHECKING:
    from lexcrawl.db.models.crawl_error import CrawlError
    from lexcrawl.db.models.localization import DocumentLocalization
    from lexcrawl.db.models.resource import DocumentResource
    from lexcrawl.db.models.source import Source

PRECISION_VALUES = (*get_args(PublicationPrecision), "unknown")
_PRECISION_RULES = (
    "(published_precision = 'day' AND published_at IS NOT NULL) OR "
    "(published_precision = 'month' AND published_month IS NOT NULL AND published_year IS NOT NULL "
    "AND published_at IS NULL) OR "
    "(published_precision = 'year' AND published_year IS NOT NULL AND published_month IS NULL "
    "AND published_at IS NULL) OR "
    "(published_precision = 'unknown' AND published_at IS NULL AND published_month IS NULL "
    "AND published_year IS NULL)"
)


class Document(Base):
    """### One logical source document, independent of language and resources."""

    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("source_id", "identity_key"),
        CheckConstraint(f"published_precision {sql_in(PRECISION_VALUES)}", name="published_precision_values"),
        CheckConstraint(_PRECISION_RULES, name="publication_precision_rules"),
        CheckConstraint(
            "published_month IS NULL OR (published_month BETWEEN 1 AND 12 AND published_year IS NOT NULL)",
            name="published_month_range",
        ),
        CheckConstraint("published_year IS NULL OR published_year BETWEEN 1800 AND 2100", name="published_year_range"),
        CheckConstraint(f"provenance {sql_in(get_args(Provenance))}", name="provenance_values"),
        CheckConstraint(
            f"extraction_status IS NULL OR extraction_status {sql_in(get_args(DocumentStatus))}",
            name="extraction_status_values",
        ),
        Index("ix_documents_source_id_published_at", "source_id", text("published_at DESC")),
        Index("ix_documents_source_id_last_seen_at", "source_id", text("last_seen_at DESC")),
        Index("ix_documents_source_id_document_type", "source_id", "document_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    source_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False)
    identity_key: Mapped[str] = mapped_column(Text, nullable=False)
    native_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    listing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    published_year: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    published_month: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    published_precision: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'unknown'"))
    published_date_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    topics: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    provenance: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'live'"))
    archive_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_listing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    source: Mapped[Source] = relationship(back_populates="documents")
    localizations: Mapped[list[DocumentLocalization]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )
    resources: Mapped[list[DocumentResource]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )
    errors: Mapped[list[CrawlError]] = relationship(back_populates="document")
