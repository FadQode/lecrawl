"""### Attachment resource entity."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, get_args

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lexcrawl.db.base import Base
from lexcrawl.db.models.checks import non_negative, sql_in
from lexcrawl.models.document import ResourceKind, ResourceStatus

if TYPE_CHECKING:
    from lexcrawl.db.models.crawl_error import CrawlError
    from lexcrawl.db.models.document import Document


class DocumentResource(Base):
    """### One attachment belonging to a logical document."""

    __tablename__ = "document_resources"
    __table_args__ = (
        UniqueConstraint("document_id", "identity_key"),
        CheckConstraint(f"resource_type {sql_in(get_args(ResourceKind))}", name="resource_type_values"),
        CheckConstraint(f"extraction_status {sql_in(get_args(ResourceStatus))}", name="extraction_status_values"),
        # $ Extraction outcome, text, and hash must stay mutually consistent.$
        CheckConstraint(
            "(extracted_text IS NULL AND content_hash IS NULL) OR "
            "(extracted_text IS NOT NULL AND content_hash IS NOT NULL)",
            name="extraction_state",
        ),
        CheckConstraint("extraction_status <> 'complete' OR extracted_text IS NOT NULL", name="complete_requires_text"),
        CheckConstraint(
            "original_url IS NOT NULL OR extraction_status = 'url_unavailable'", name="url_unavailable_state"
        ),
        CheckConstraint(non_negative("file_size_bytes"), name="file_size_non_negative"),
        CheckConstraint(non_negative("page_count"), name="page_count_non_negative"),
        CheckConstraint(non_negative("character_count"), name="character_count_non_negative"),
        Index("ix_document_resources_document_id_extraction_status", "document_id", "extraction_status"),
        Index("ix_document_resources_extraction_status_updated_at", "extraction_status", text("updated_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    document_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    identity_key: Mapped[str] = mapped_column(Text, nullable=False)
    native_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    display_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    resource_type: Mapped[str] = mapped_column(String(16), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    character_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_status: Mapped[str] = mapped_column(String(24), nullable=False)
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    archive_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    document: Mapped[Document] = relationship(back_populates="resources")
    errors: Mapped[list[CrawlError]] = relationship(back_populates="resource")
