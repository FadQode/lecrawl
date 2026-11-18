"""### Source registry reads and writes."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from lexcrawl.db.models import Source

# $ Fallback seed used only when the source registry is unavailable.$
SEED_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("uae_legislation_news", "UAE Legislation news", "https://uaelegislation.gov.ae"),
    ("cba_news_releases", "CBA news releases", "https://cba.ca"),
    ("commerce_reports", "U.S. Department of Commerce reports", "https://www.commerce.gov"),
)


def source_id_for(session: Session, code: str) -> int | None:
    """### Return the id registered for a source code, or None."""
    return session.scalar(select(Source.id).where(Source.code == code))


def ensure_source(session: Session, code: str, name: str, base_url: str | None = None) -> int:
    """### Return the source id, inserting the registry row when missing."""
    existing = source_id_for(session, code)
    if existing is not None:
        return existing
    source = Source(code=code, name=name, base_url=base_url)
    session.add(source)
    session.flush()
    return source.id


def seed_sources(session: Session, rows: Sequence[tuple[str, str, str | None]]) -> int:
    """### Insert any missing sources from the registry rows; return how many were created."""
    created = 0
    for code, name, base_url in rows:
        if source_id_for(session, code) is None:
            ensure_source(session, code, name, base_url)
            created += 1
    return created
