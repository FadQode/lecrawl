"""### PostgreSQL advisory locks to keep scheduled and manual runs from overlapping."""

from __future__ import annotations

import hashlib

from sqlalchemy import Connection, Engine, text


def advisory_lock_key_for(source: str) -> int:
    """### Derive a stable 64-bit advisory lock key from a source name.

    Sources take independent locks so one slow source cannot block the others.
    """
    digest = hashlib.sha256(source.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


class RunInProgressError(RuntimeError):
    """### Raised when another process holds the crawl advisory lock."""


class LockUnavailableError(RuntimeError):
    """### Raised when a process-scoped advisory lock is already held."""


# $ Shared by the scheduler and the API worker so they cannot run simultaneously.$
ADVISORY_LOCK_KEY = 0x1E5C2A11

# $ Held by the `schedule` process so only one scheduler instance runs at a time.$
SCHEDULER_ADVISORY_LOCK_KEY = 0x1E5C2A12


class AdvisoryLock:
    """### A session-level PostgreSQL advisory lock held on a dedicated connection.

    The connection must stay open for the whole crawl; the lock is released when it
    closes, so a crashed worker frees it automatically.
    """

    def __init__(self, engine: Engine, key: int = ADVISORY_LOCK_KEY) -> None:
        """### Record the engine and lock key."""
        self._engine = engine
        self._key = key
        self._connection: Connection | None = None

    def acquire(self) -> bool:
        """### Try to take the lock, returning False when it is already held."""
        connection = self._engine.connect()
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
        connection = self._connection
        if connection is None:
            return
        try:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": self._key})
            connection.commit()
        finally:
            connection.close()
            self._connection = None
