"""### Source registry seeding."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from lexcrawl.db.models import Source


@dataclass(frozen=True, slots=True)
class SourceSeed:
    """### Registry entry for one supported source."""

    code: str
    name: str


# $ Codes are the canonical source identifiers used across the application.$
SOURCE_REGISTRY: tuple[SourceSeed, ...] = (
    SourceSeed(code="uae_legislation_news", name="UAE Legislation"),
    SourceSeed(code="cba_news_releases", name="Canadian Bar Association"),
    SourceSeed(code="commerce_reports", name="U.S. Department of Commerce"),
)

SOURCE_CODES = tuple(entry.code for entry in SOURCE_REGISTRY)


def seed_sources(session: Session) -> int:
    """### Insert missing registry rows and return how many were created."""
    statement = (
        insert(Source)
        .values([{"code": entry.code, "name": entry.name} for entry in SOURCE_REGISTRY])
        .on_conflict_do_nothing(index_elements=[Source.code])
        .returning(Source.code)
    )
    created = session.execute(statement).scalars().all()
    return len(created)


def source_id_for(session: Session, code: str) -> int | None:
    """### Return the registry identifier for a source code."""
    return session.execute(select(Source.id).where(Source.code == code)).scalar_one_or_none()
