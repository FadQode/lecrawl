"""### Change detection: decide whether a source needs a crawl.

The decision compares the freshly observed fingerprint against the **processed**
fingerprint (what has actually been ingested), never merely the last observation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from lexcrawl.checks.base import CheckOutcome, CheckProbe
from lexcrawl.checks.checkpoints import CheckpointStore


@dataclass(frozen=True, slots=True)
class Decision:
    """### The outcome of one change-detection decision."""

    source: str
    outcome: CheckOutcome
    probe: CheckProbe
    processed_fingerprint: str | None
    processed_at: datetime | None


class ChangeDetectionService:
    """### Coordinate a probe with the stored checkpoint."""

    def __init__(self, store: CheckpointStore, *, record: bool = True) -> None:
        """### Store the checkpoint store and whether writes are allowed."""
        self._store = store
        self._record = record

    def decide(self, source: str, probe: CheckProbe, *, force: bool = False) -> Decision:
        """### Compare against the processed fingerprint and record the observation."""
        if not probe.ok:
            if self._record:
                self._store.record_failure(source, probe.error or "probe failed")
            return Decision(source, "check_failed", probe, None, None)

        state = self._store.get(source)
        outcome: CheckOutcome = (
            "new_data" if force or probe.fingerprint != state.processed_fingerprint else "no_change"
        )
        if self._record:
            self._store.record_observed(source, probe.fingerprint, outcome)
        return Decision(source, outcome, probe, state.processed_fingerprint, state.processed_at)

    def mark_processed(self, source: str, fingerprint: str | None) -> None:
        """### Advance the processed fingerprint (call only after a successful ingest)."""
        if self._record:
            self._store.advance_processed(source, fingerprint)
