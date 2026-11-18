"""### Identity memory contract: which documents have been ingested, per source."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from lexcrawl.models.document import CanonicalDocument


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

    def filter_known(self, source: str, keys: Sequence[str]) -> frozenset[str]:
        """### Return which candidate identities were already ingested (batched)."""
        ...

    def has_baseline(self, source: str) -> bool:
        """### Report whether the source has at least one ingested document."""
        ...

    def remember(self, source: str, documents: Sequence[CanonicalDocument]) -> RememberResult:
        """### Record the processed documents, returning what was recorded."""
        ...

    def due_count(self, source: str) -> int:
        """### Return how many documents are still due for retry."""
        ...

    def retry_due(self, source: str) -> int:
        """### Retry due attachments for a source; return how many were processed."""
        ...

    def close(self) -> None:
        """### Release any resources."""
        ...
