"""### Advisory file locking to keep scheduled and manual runs from overlapping."""

from __future__ import annotations

import fcntl
from pathlib import Path
from types import TracebackType
from typing import IO


class FileLock:
    """### A non-blocking advisory lock backed by a file descriptor.

    The kernel releases the lock when the process exits, so a crash never leaves a
    permanent lock behind.
    """

    def __init__(self, path: Path) -> None:
        """### Record the lock file location."""
        self._path = path
        self._handle: IO[str] | None = None

    def acquire(self) -> bool:
        """### Try to take the lock, returning False when it is already held."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("w")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        """### Release the lock if this instance holds it."""
        handle = self._handle
        if handle is not None:
            fcntl.flock(handle, fcntl.LOCK_UN)
            handle.close()
            self._handle = None

    def __enter__(self) -> FileLock:
        """### Acquire the lock, raising if it is already held."""
        if not self.acquire():
            raise LockUnavailableError(f"lock already held: {self._path}")
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        """### Release the lock on exit."""
        self.release()


class LockUnavailableError(RuntimeError):
    """### Raised when a lock is already held by another process."""


# $ Shared by the scheduler and the API worker so they cannot run simultaneously.$
ADVISORY_LOCK_KEY = 0x1E5C2A11


class RunInProgressError(RuntimeError):
    """### Raised when another process holds the crawl advisory lock."""


class AdvisoryLock:
    """### A session-level PostgreSQL advisory lock held on a dedicated connection.

    The connection must stay open for the whole crawl; the lock is released when it
    closes, so a crashed worker frees it automatically.
    """

    def __init__(self, engine: object, key: int = ADVISORY_LOCK_KEY) -> None:
        """### Record the engine and lock key."""
        self._engine = engine
        self._key = key
        self._connection: object | None = None

    def acquire(self) -> bool:
        """### Try to take the lock, returning False when it is already held."""
        from sqlalchemy import text

        connection = self._engine.connect()  # type: ignore[attr-defined]
        try:
            got = connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": self._key}).scalar()
        except Exception:
            connection.close()
            raise
        if not got:
            connection.close()
            return False
        self._connection = connection
        return True

    def release(self) -> None:
        """### Release the lock and close the dedicated connection."""
        from sqlalchemy import text

        connection = self._connection
        if connection is None:
            return
        try:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self._key})  # type: ignore[attr-defined]
            connection.commit()  # type: ignore[attr-defined]
        finally:
            connection.close()  # type: ignore[attr-defined]
            self._connection = None
