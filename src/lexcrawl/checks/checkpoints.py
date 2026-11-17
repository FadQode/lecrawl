"""### Checkpoint store: observed vs processed fingerprints per source.

The database is the application default; a JSON store remains for tests and explicit
local development.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import Engine

from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.repositories import checkpoints as repo
from lexcrawl.repositories.errors import PersistenceError
from lexcrawl.repositories.sources import source_id_for
from lexcrawl.state_io import read_json_state, write_json_state


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


class JsonCheckpointStore:
    """### JSON-backed checkpoint store for tests and local development."""

    def __init__(self, path: Path) -> None:
        """### Record the state file location."""
        self._path = path

    def get(self, source: str) -> CheckpointState:
        """### Return the current checkpoint state."""
        entry = self._load().get(source)
        if not isinstance(entry, dict):
            return CheckpointState()
        return CheckpointState(
            observed_fingerprint=_as_str(entry.get("observed")),
            processed_fingerprint=_as_str(entry.get("processed")),
            processed_at=_as_datetime(entry.get("processed_at")),
            last_outcome=_as_str(entry.get("last_outcome")),
        )

    def record_observed(self, source: str, fingerprint: str | None, outcome: str) -> None:
        """### Record the latest observed fingerprint and outcome."""
        state = self._load()
        entry = _entry(state, source)
        entry["observed"] = fingerprint
        entry["observed_at"] = _now().isoformat()
        entry["last_outcome"] = outcome
        entry.pop("last_error", None)
        write_json_state(self._path, state)

    def advance_processed(self, source: str, fingerprint: str | None) -> None:
        """### Advance the processed fingerprint."""
        state = self._load()
        entry = _entry(state, source)
        entry["processed"] = fingerprint
        entry["processed_at"] = _now().isoformat()
        write_json_state(self._path, state)

    def record_failure(self, source: str, error: str) -> None:
        """### Record a failed probe."""
        state = self._load()
        entry = _entry(state, source)
        entry["last_outcome"] = "check_failed"
        entry["last_error"] = error
        write_json_state(self._path, state)

    def close(self) -> None:
        """### No resources to release."""
        return None

    def _load(self) -> dict[str, Any]:
        """### Load the state file, surfacing corruption."""
        return read_json_state(self._path)


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
            repo.advance_processed(
                session, self._require(session, source), fingerprint=fingerprint, now=_now()
            )

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


def _entry(state: dict[str, Any], source: str) -> dict[str, Any]:
    """### Return the mutable entry for a source, creating it when missing."""
    entry = state.get(source)
    if not isinstance(entry, dict):
        entry = {}
        state[source] = entry
    return entry


def _as_str(value: object) -> str | None:
    """### Return a string value or None."""
    return value if isinstance(value, str) else None


def _as_datetime(value: object) -> datetime | None:
    """### Parse an ISO timestamp, returning None when unusable."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _now() -> datetime:
    """### Return the current UTC time."""
    return datetime.now(tz=UTC)
