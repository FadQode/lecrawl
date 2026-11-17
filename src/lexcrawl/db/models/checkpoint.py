"""### Per-source change-detection checkpoints (observed vs processed)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column

from lexcrawl.db.base import Base


class SourceCheckpoint(Base):
    """### The observed and processed fingerprints for one source."""

    __tablename__ = "source_checkpoints"

    source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    # $ Observed is what the last successful probe saw; processed is what we have ingested.$
    observed_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
