"""### Persisted set of already-ingested document identities.

Interim JSON store; it moves behind the same interface when the PostgreSQL
identity table lands.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class IdentityStore:
    """### Track, per source, which document identities have been ingested."""

    def __init__(self, path: Path) -> None:
        """### Record the state file location."""
        self._path = path

    @property
    def path(self) -> Path:
        """### Return the state file path."""
        return self._path

    def known(self, source: str) -> frozenset[str]:
        """### Return the identities already ingested for a source."""
        values = self._load().get(source, [])
        return frozenset(value for value in values if isinstance(value, str))

    def add(self, source: str, identities: Iterable[str]) -> int:
        """### Record identities as ingested, returning how many were newly added."""
        current = set(self.known(source))
        added = {identity for identity in identities if identity and identity not in current}
        if not added:
            return 0
        state = self._load()
        state[source] = sorted(current | added)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return len(added)

    def _load(self) -> dict[str, list[Any]]:
        """### Load the state file, tolerating absence or corruption."""
        if not self._path.is_file():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {key: value for key, value in payload.items() if isinstance(value, list)}
