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
