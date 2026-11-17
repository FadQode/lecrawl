"""### Shared pytest fixtures for PostgreSQL-backed tests."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import SQLAlchemyError

from lexcrawl.config import get_settings
from lexcrawl.db.engine import create_db_engine
from lexcrawl.db.session import create_session_factory


@pytest.fixture
def pg_engine():  # type: ignore[no-untyped-def]
    """### Provide a live engine, or skip when PostgreSQL is unreachable."""
    settings = get_settings()
    if not settings.database_url.strip():
        pytest.skip("DATABASE_URL is not configured")
    engine = create_db_engine(settings)
    try:
        with engine.connect():
            pass
    except SQLAlchemyError:
        engine.dispose()
        pytest.skip("PostgreSQL is not reachable")
    yield engine
    engine.dispose()


@pytest.fixture
def pg_factory(pg_engine):  # type: ignore[no-untyped-def]
    """### Provide a session factory bound to the live engine."""
    return create_session_factory(pg_engine)
