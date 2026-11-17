"""### Persisted set of already-ingested document identities.

Interim JSON store; it moves behind the same interface when the PostgreSQL
identity table lands.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from lexcrawl.models.document import CanonicalDocument
from lexcrawl.state_io import read_json_state, write_json_state


@dataclass(frozen=True, slots=True)
class RememberResult:
    """### What a persistence pass recorded."""

    documents: int = 0
    inserted: int = 0
    updated: int = 0
    resources: int = 0


class DocumentMemory(Protocol):
    """### Remembers which document identities have been ingested, per source."""

    def known(self, source: str) -> frozenset[str]:
        """### Return the identities already ingested for a source."""
        ...

    def remember(self, source: str, documents: Sequence[CanonicalDocument]) -> RememberResult:
        """### Record the processed documents, returning what was recorded."""
        ...

    def due_count(self, source: str) -> int:
        """### Return how many documents are still due for retry."""
        ...

    def close(self) -> None:
        """### Release any resources."""
        ...


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
        write_json_state(self._path, state)
        return len(added)

    def _load(self) -> dict[str, list[Any]]:
        """### Load the state file, surfacing (not swallowing) corruption."""
        payload = read_json_state(self._path)
        return {key: value for key, value in payload.items() if isinstance(value, list)}


class JsonDocumentMemory:
    """### JSON-backed identity memory: tracks only the identities of complete documents."""

    def __init__(self, path: Path) -> None:
        """### Wrap a JSON identity store at the given path."""
        self._store = IdentityStore(path)

    def known(self, source: str) -> frozenset[str]:
        """### Return the identities already ingested for a source."""
        return self._store.known(source)

    def remember(self, source: str, documents: Sequence[CanonicalDocument]) -> RememberResult:
        """### Record completed identities and report approximate insert/update counts."""
        known_before = self._store.known(source)
        completed = [
            document
            for document in documents
            if document.identity_key is not None and document.extraction_status == "complete"
        ]
        inserted = sum(1 for document in completed if document.identity_key not in known_before)
        self._store.add(source, [str(document.identity_key) for document in completed])
        resources = sum(len(document.resources) for document in documents)
        return RememberResult(
            documents=len(documents),
            inserted=inserted,
            updated=len(completed) - inserted,
            resources=resources,
        )

    def due_count(self, source: str) -> int:
        """### JSON memory does not track pending documents for retry."""
        return 0

    def close(self) -> None:
        """### No resources to release."""
        return None
