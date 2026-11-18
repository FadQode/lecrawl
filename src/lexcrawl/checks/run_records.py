"""### Crawl-run recording: persist per-source outcomes, credits, and errors.

The database recorder is used for the database backend; a no-op recorder keeps JSON mode
and most tests working without PostgreSQL.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import Engine

from lexcrawl.checks.base import CheckOutcome
from lexcrawl.crawling.base import redact_message
from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.repositories import crawl_errors as errors_repo
from lexcrawl.repositories import crawl_runs as runs_repo
from lexcrawl.repositories.sources import source_id_for


@dataclass(frozen=True, slots=True)
class RunHandle:
    """### A started run: internal id (for FKs) and public run_id (for logs)."""

    run_id_internal: int
    run_id: str


def classify_error(message: str) -> tuple[str, str]:
    """### Classify an error message into (stage, error_type)."""
    lowered = message.lower()
    if any(token in lowered for token in ("pdf", "attachment", "resource")):
        return "pdf", "resource_error"
    if any(token in lowered for token in ("listing", "feed", "sitemap")):
        return "listing", "fetch_error"
    if any(token in lowered for token in ("detail", "article", "report")):
        return "detail", "extraction_error"
    if any(token in lowered for token in ("fetch", "http", "firecrawl", "timeout")):
        return "detail", "fetch_error"
    if any(token in lowered for token in ("persist", "database", "checkpoint")):
        return "persistence", "persistence_error"
    return "unknown", "actor_error"


class RunRecorder(Protocol):
    """### Record a crawl run, its per-source metrics, and its errors."""

    def start(self, trigger_type: str, run_id: str | None = None) -> RunHandle | None:
        """### Start a run and return its handle."""
        ...

    def record_source(
        self,
        run_id: int,
        source: str,
        *,
        outcome: CheckOutcome,
        status: str,
        discovered: int,
        inserted: int,
        updated: int,
        skipped: int,
        failed: int,
        resources: int,
        pending: int,
        credits: int,
        pagination_complete: bool,
        detail: str,
        started_at: datetime,
        finished_at: datetime,
    ) -> int | None:
        """### Record one source's outcome; return its row id."""
        ...

    def record_errors(self, crawl_run_source_id: int, errors: Sequence[str]) -> None:
        """### Persist sanitized errors for a source run."""
        ...

    def finish(self, run_id: int, *, status: str, credits: int, duration_ms: int) -> None:
        """### Finish the run with its status, credits, and duration."""
        ...

    def close(self) -> None:
        """### Release resources."""
        ...


class DbRunRecorder:
    """### Persist crawl runs, per-source metrics, and sanitized errors to PostgreSQL."""

    def __init__(self, engine: Engine, *, owns_engine: bool = True) -> None:
        """### Bind to an engine and build a session factory."""
        self._engine = engine
        self._owns_engine = owns_engine
        self._factory = create_session_factory(engine)

    def start(self, trigger_type: str, run_id: str | None = None) -> RunHandle | None:
        """### Insert a running crawl-run row."""
        parsed = uuid.UUID(run_id) if run_id else None
        with session_scope(self._factory) as session:
            run_internal_id, run_uuid = runs_repo.start_run(
                session, trigger_type=trigger_type, now=_now(), run_id=parsed
            )
        return RunHandle(run_id_internal=run_internal_id, run_id=str(run_uuid))

    def record_source(
        self,
        run_id: int,
        source: str,
        *,
        outcome: CheckOutcome,
        status: str,
        discovered: int,
        inserted: int,
        updated: int,
        skipped: int,
        failed: int,
        resources: int,
        pending: int,
        credits: int,
        pagination_complete: bool,
        detail: str,
        started_at: datetime,
        finished_at: datetime,
    ) -> int | None:
        """### Insert the per-source metrics for the run."""
        with session_scope(self._factory) as session:
            source_id = source_id_for(session, source)
            if source_id is None:
                return None
            return runs_repo.record_source(
                session,
                crawl_run_id=run_id,
                source_id=source_id,
                check_outcome=outcome,
                status=status,
                discovered=discovered,
                inserted=inserted,
                updated=updated,
                skipped=skipped,
                failed=failed,
                resources=resources,
                pending=pending,
                credits=credits,
                pagination_complete=pagination_complete,
                detail=detail,
                started_at=started_at,
                finished_at=finished_at,
            )

    def record_errors(self, crawl_run_source_id: int, errors: Sequence[str]) -> None:
        """### Persist each sanitized error with its classified stage and type."""
        if not errors:
            return
        now = _now()
        with session_scope(self._factory) as session:
            for message in errors:
                stage, error_type = classify_error(message)
                errors_repo.add_error(
                    session,
                    crawl_run_source_id=crawl_run_source_id,
                    stage=stage,
                    error_type=error_type,
                    message=redact_message(message),
                    now=now,
                )

    def finish(self, run_id: int, *, status: str, credits: int, duration_ms: int) -> None:
        """### Finish the run."""
        with session_scope(self._factory) as session:
            runs_repo.finish_run(
                session, crawl_run_id=run_id, status=status, credits=credits, duration_ms=duration_ms, now=_now()
            )

    def close(self) -> None:
        """### Dispose the engine's pool only when this component owns it."""
        if self._owns_engine:
            self._engine.dispose()


def _now() -> datetime:
    """### Return the current UTC time."""
    return datetime.now(tz=UTC)
