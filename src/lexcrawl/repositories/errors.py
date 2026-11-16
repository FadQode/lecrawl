"""### Shared persistence errors."""

from __future__ import annotations


class PersistenceError(RuntimeError):
    """### Raised when the persistence layer cannot complete a write."""
