"""### Change-detection checkpoint reads and writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from lexcrawl.db.models import SourceCheckpoint


@dataclass(frozen=True, slots=True)
class CheckpointState:
    """### Observed/processed fingerprints for a source."""

    observed_fingerprint: str | None = None
    processed_fingerprint: str | None = None
    processed_at: datetime | None = None
    last_outcome: str | None = None
    last_error: str | None = None


def _get_or_create(session: Session, source_id: int, now: datetime) -> SourceCheckpoint:
    """### Return the checkpoint row, creating an empty one when missing."""
    row = session.get(SourceCheckpoint, source_id)
    if row is None:
        row = SourceCheckpoint(source_id=source_id, updated_at=now)
        session.add(row)
    return row


def get_checkpoint(session: Session, source_id: int) -> CheckpointState:
    """### Return the checkpoint state for a source."""
    row = session.get(SourceCheckpoint, source_id)
    if row is None:
        return CheckpointState()
    return CheckpointState(
        observed_fingerprint=row.observed_fingerprint,
        processed_fingerprint=row.processed_fingerprint,
        processed_at=row.processed_at,
        last_outcome=row.last_outcome,
        last_error=row.last_error,
    )


def record_observed(
    session: Session, source_id: int, *, fingerprint: str | None, outcome: str, now: datetime
) -> None:
    """### Record what the latest successful probe observed."""
    row = _get_or_create(session, source_id, now)
    row.observed_fingerprint = fingerprint
    row.observed_at = now
    row.last_outcome = outcome
    row.last_error = None
    row.updated_at = now


def advance_processed(session: Session, source_id: int, *, fingerprint: str | None, now: datetime) -> None:
    """### Advance the processed fingerprint after a successful ingest."""
    row = _get_or_create(session, source_id, now)
    row.processed_fingerprint = fingerprint
    row.processed_at = now
    row.updated_at = now


def record_failure(session: Session, source_id: int, *, error: str, now: datetime) -> None:
    """### Record a failed probe without touching either fingerprint."""
    row = _get_or_create(session, source_id, now)
    row.last_outcome = "check_failed"
    row.last_error = error
    row.updated_at = now
