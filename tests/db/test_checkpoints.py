"""### PostgreSQL test for the DB checkpoint store."""

from __future__ import annotations

import pytest
from sqlalchemy import delete

from lexcrawl.checks.checkpoints import DbCheckpointStore
from lexcrawl.db.models import Source
from lexcrawl.db.session import session_scope
from lexcrawl.repositories.sources import ensure_source

TEST_SOURCE = "test_checkpoint_source"


@pytest.mark.db
def test_db_checkpoint_store(pg_engine, pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### Observed/processed/outcome round-trip through the database."""
    with session_scope(pg_factory) as session:
        session.execute(delete(Source).where(Source.code == TEST_SOURCE))
    with session_scope(pg_factory) as session:
        ensure_source(session, TEST_SOURCE, "Checkpoint source")
    store = DbCheckpointStore(pg_engine)
    try:
        assert store.get(TEST_SOURCE).processed_fingerprint is None
        store.record_observed(TEST_SOURCE, "fp1", "new_data")
        assert store.get(TEST_SOURCE).observed_fingerprint == "fp1"
        store.advance_processed(TEST_SOURCE, "fp1")
        assert store.get(TEST_SOURCE).processed_fingerprint == "fp1"
        store.record_failure(TEST_SOURCE, "boom")
        assert store.get(TEST_SOURCE).last_outcome == "check_failed"
    finally:
        with session_scope(pg_factory) as session:
            session.execute(delete(Source).where(Source.code == TEST_SOURCE))
        store.close()
