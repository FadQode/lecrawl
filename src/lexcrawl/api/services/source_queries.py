"""### Database queries for configured sources."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from lexcrawl.db.models import Source


def list_sources(session: Session) -> list[Source]:
    """### Return every configured source ordered by code."""
    statement = select(Source).order_by(Source.code)
    return list(session.execute(statement).scalars())


def get_source(session: Session, source_code: str) -> Source | None:
    """### Return one source by its code, or None."""
    statement = select(Source).where(Source.code == source_code)
    return session.execute(statement).scalar_one_or_none()
