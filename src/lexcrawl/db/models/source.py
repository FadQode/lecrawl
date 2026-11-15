"""### Registry of supported source websites."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Identity, Integer, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from lexcrawl.db.base import Base


class Source(Base):
    """### One supported crawled source, addressed by a stable code."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, Identity(), primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sql_text("true"))
    # $ Per-source overrides only; secrets and global defaults stay in the environment.$
    config: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
