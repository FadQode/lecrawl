"""### Per-language localization entity."""

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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lexcrawl.db.base import Base
from lexcrawl.db.models.checks import sql_in
from lexcrawl.models.document import TranslationStatus

if TYPE_CHECKING:
    from lexcrawl.db.models.crawl_error import CrawlError
    from lexcrawl.db.models.document import Document


class DocumentLocalization(Base):
    """### One language variant of a logical document."""

    __tablename__ = "document_localizations"
    __table_args__ = (
        UniqueConstraint("document_id", "language_code"),
        CheckConstraint(f"status {sql_in(get_args(TranslationStatus))}", name="status_values"),
        # $ Only completed localizations may carry content, and content requires its hash.$
        CheckConstraint(
            "(text_content IS NULL AND content_hash IS NULL) OR "
            "(text_content IS NOT NULL AND content_hash IS NOT NULL)",
            name="content_state",
        ),
        CheckConstraint("status <> 'complete' OR text_content IS NOT NULL", name="complete_requires_content"),
        Index("ix_document_localizations_language_code_status", "language_code", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    language_code: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    listing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    document: Mapped[Document] = relationship(back_populates="localizations")
    errors: Mapped[list[CrawlError]] = relationship(back_populates="localization")
