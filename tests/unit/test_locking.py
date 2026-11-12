"""### Unit tests for the advisory file lock."""

from __future__ import annotations

from pathlib import Path

import pytest

from lexcrawl.locking import FileLock, LockUnavailableError


def test_acquire_and_release(tmp_path: Path) -> None:
    """### A released lock can be taken again."""
    lock = FileLock(tmp_path / "x.lock")
    assert lock.acquire() is True
    lock.release()
    assert FileLock(tmp_path / "x.lock").acquire() is True


def test_second_lock_contends(tmp_path: Path) -> None:
    """### A second holder cannot take a held lock."""
    first = FileLock(tmp_path / "x.lock")
    second = FileLock(tmp_path / "x.lock")
    assert first.acquire() is True
    assert second.acquire() is False
    first.release()
    assert second.acquire() is True


def test_context_manager_raises_when_busy(tmp_path: Path) -> None:
    """### Entering a held lock raises."""
    holder = FileLock(tmp_path / "x.lock")
    holder.acquire()
    with pytest.raises(LockUnavailableError), FileLock(tmp_path / "x.lock"):
        pass
    holder.release()
