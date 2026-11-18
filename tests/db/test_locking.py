"""### PostgreSQL tests for the advisory locking helpers."""

from __future__ import annotations

import pytest

from lexcrawl.locking import (
    AdvisoryLock,
    LockUnavailableError,
    RunInProgressError,
    advisory_lock_key_for,
)


def test_advisory_lock_key_is_stable_and_distinct() -> None:
    """### Keys are deterministic per source and differ across sources."""
    assert advisory_lock_key_for("uae") == advisory_lock_key_for("uae")
    assert advisory_lock_key_for("uae") != advisory_lock_key_for("cba")
    assert advisory_lock_key_for("uae") != advisory_lock_key_for("commerce")


def test_lock_errors_are_runtime_errors() -> None:
    """### Both lock errors are RuntimeError subclasses."""
    assert issubclass(RunInProgressError, RuntimeError)
    assert issubclass(LockUnavailableError, RuntimeError)


@pytest.mark.db
def test_advisory_lock_is_exclusive_and_releasable(pg_engine) -> None:  # type: ignore[no-untyped-def]
    """### A second holder is refused while the first holds the lock."""
    first = AdvisoryLock(pg_engine)
    second = AdvisoryLock(pg_engine)
    try:
        assert first.acquire() is True
        assert second.acquire() is False
        first.release()
        assert second.acquire() is True
    finally:
        first.release()
        second.release()


@pytest.mark.db
def test_advisory_lock_by_key_is_independent(pg_engine) -> None:  # type: ignore[no-untyped-def]
    """### Different source keys do not block each other."""
    uae = AdvisoryLock(pg_engine, key=advisory_lock_key_for("uae"))
    cba = AdvisoryLock(pg_engine, key=advisory_lock_key_for("cba"))
    try:
        assert uae.acquire() is True
        assert cba.acquire() is True
    finally:
        uae.release()
        cba.release()


@pytest.mark.db
def test_release_without_acquire_is_a_noop(pg_engine) -> None:  # type: ignore[no-untyped-def]
    """### Releasing a lock that was never acquired is safe."""
    AdvisoryLock(pg_engine).release()
