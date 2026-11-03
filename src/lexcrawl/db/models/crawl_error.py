"""### Structured crawl error entity."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal, get_args

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lexcrawl.db.base import Base
from lexcrawl.db.models.checks import sql_in

if TYPE_CHECKING:
    from lexcrawl.db.models.crawl_run import CrawlRunSource
    from lexcrawl.db.models.document import Document
    from lexcrawl.db.models.localization import DocumentLocalization
    from lexcrawl.db.models.resource import DocumentResource

ErrorStage = Literal["discovery", "fetch", "parse", "validate", "extract", "persistence", "unknown"]
STAGE_VALUES = get_args(ErrorStage)


class CrawlError(Base):
    """### One structured error attributed to a run, document, localization, or resource."""

    __tablename__ = "crawl_errors"
    __table_args__ = (
        CheckConstraint(f"stage {sql_in(STAGE_VALUES)}", name="stage_values"),
        # $ An error points at most at one entity, so its attribution stays unambiguous.$
        CheckConstraint("num_nonnulls(document_id, localization_id, resource_id) <= 1", name="single_attribution"),
        Index("ix_crawl_errors_crawl_run_source_id_occurred_at", "crawl_run_source_id", text("occurred_at DESC")),
        Index("ix_crawl_errors_error_type_occurred_at", "error_type", text("occurred_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    crawl_run_source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("crawl_run_sources.id", ondelete="CASCADE"), nullable=False
    )
    document_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    localization_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("document_localizations.id", ondelete="SET NULL"), nullable=True
    )
    resource_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("document_resources.id", ondelete="SET NULL"), nullable=True
    )
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    error_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run_source: Mapped[CrawlRunSource] = relationship(back_populates="errors")
    document: Mapped[Document | None] = relationship(back_populates="errors")
    localization: Mapped[DocumentLocalization | None] = relationship(back_populates="errors")
    resource: Mapped[DocumentResource | None] = relationship(back_populates="errors")
