"""### DB test for seeding the sources table from the registry."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from lexcrawl.db.models import Source
from lexcrawl.db.session import session_scope
from lexcrawl.factory import ensure_registry_sources
from lexcrawl.repositories.sources import seed_sources, source_id_for

_SYNTHETIC = "test_seeded_source"


@pytest.mark.db
def test_seed_sources_is_idempotent(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### Seeding creates a missing source once and is a no-op afterwards."""
    with session_scope(pg_factory) as session:
        session.execute(delete(Source).where(Source.code == _SYNTHETIC))
    try:
        with session_scope(pg_factory) as session:
            assert seed_sources(session, [(_SYNTHETIC, "Seeded", "https://seed.test")]) == 1
        with session_scope(pg_factory) as session:
            assert source_id_for(session, _SYNTHETIC) is not None
            assert seed_sources(session, [(_SYNTHETIC, "Seeded", "https://seed.test")]) == 0
    finally:
        with session_scope(pg_factory) as session:
            session.execute(delete(Source).where(Source.code == _SYNTHETIC))


@pytest.mark.db
def test_registry_sources_are_present(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### Seeding from the registry is idempotent and the sources exist afterwards."""
    assert ensure_registry_sources(pg_factory) == 0
    with session_scope(pg_factory) as session:
        assert all(
            source_id_for(session, code) is not None
            for code in ("uae_legislation_news", "cba_news_releases", "commerce_reports")
        )
