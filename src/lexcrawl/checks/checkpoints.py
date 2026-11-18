"""### Checkpoint store: observed vs processed fingerprints per source (PostgreSQL)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import Engine

from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.repositories import checkpoints as repo
from lexcrawl.repositories.errors import PersistenceError
from lexcrawl.repositories.sources import source_id_for


@dataclass(frozen=True, slots=True)
class CheckpointState:
    """### The fingerprint state a change decision is based on."""

    observed_fingerprint: str | None = None
    processed_fingerprint: str | None = None
    processed_at: datetime | None = None
    last_outcome: str | None = None


class CheckpointStore(Protocol):
    """### Read/write checkpoint state for a source."""

    def get(self, source: str) -> CheckpointState:
        """### Return the current checkpoint state."""
        ...

    def record_observed(self, source: str, fingerprint: str | None, outcome: str) -> None:
        """### Record the latest observed fingerprint and outcome."""
        ...

    def advance_processed(self, source: str, fingerprint: str | None) -> None:
        """### Advance the processed fingerprint after a successful ingest."""
        ...

    def record_failure(self, source: str, error: str) -> None:
        """### Record a failed probe."""
        ...

    def close(self) -> None:
        """### Release resources."""
        ...


class DbCheckpointStore:
    """### PostgreSQL-backed checkpoint store."""

    def __init__(self, engine: Engine, *, owns_engine: bool = True) -> None:
        """### Bind to an engine and build a session factory."""
        self._engine = engine
        self._owns_engine = owns_engine
        self._factory = create_session_factory(engine)

    def get(self, source: str) -> CheckpointState:
        """### Return the current checkpoint state."""
        with session_scope(self._factory) as session:
            source_id = source_id_for(session, source)
            if source_id is None:
                return CheckpointState()
            state = repo.get_checkpoint(session, source_id)
        return CheckpointState(
            observed_fingerprint=state.observed_fingerprint,
            processed_fingerprint=state.processed_fingerprint,
            processed_at=state.processed_at,
            last_outcome=state.last_outcome,
        )

    def record_observed(self, source: str, fingerprint: str | None, outcome: str) -> None:
        """### Record the latest observed fingerprint and outcome."""
        with session_scope(self._factory) as session:
            repo.record_observed(
                session, self._require(session, source), fingerprint=fingerprint, outcome=outcome, now=_now()
            )

    def advance_processed(self, source: str, fingerprint: str | None) -> None:
        """### Advance the processed fingerprint."""
        with session_scope(self._factory) as session:
            repo.advance_processed(session, self._require(session, source), fingerprint=fingerprint, now=_now())

    def record_failure(self, source: str, error: str) -> None:
        """### Record a failed probe."""
        with session_scope(self._factory) as session:
            repo.record_failure(session, self._require(session, source), error=error, now=_now())

    def close(self) -> None:
        """### Dispose the engine's pool only when this component owns it."""
        if self._owns_engine:
            self._engine.dispose()

    @staticmethod
    def _require(session: Any, source: str) -> int:
        """### Return the source id, failing if the source is not registered."""
        source_id = source_id_for(session, source)
        if source_id is None:
            raise PersistenceError(f"source {source!r} is not registered; run the seed migration")
        return source_id


def _now() -> datetime:
    """### Return the current UTC time."""
    return datetime.now(tz=UTC)
