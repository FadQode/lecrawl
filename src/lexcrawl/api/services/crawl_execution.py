"""### Background crawl execution: run lifecycle, advisory locks, and worker pool."""

from __future__ import annotations

import hashlib
from argparse import Namespace
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl import cli
from lexcrawl.actors.base import Actor
from lexcrawl.api.exceptions import ConflictError
from lexcrawl.config import Settings
from lexcrawl.crawling.base import Fetcher
from lexcrawl.db.session import session_scope
from lexcrawl.logging import get_logger
from lexcrawl.orchestrator import CrawlRunReport, run_crawl
from lexcrawl.persistence import persist_crawl_report, persist_run_failure
from lexcrawl.repositories.crawl_runs import create_run

LOGGER = get_logger(__name__)

# $ Source option -> persisted source code(s) for the selected scope.$
SOURCE_CODES: dict[str, tuple[str, ...]] = {
    "uae": ("uae_legislation_news",),
    "cba": ("cba_news_releases",),
    "commerce": ("commerce_reports",),
    "all": ("uae_legislation_news", "cba_news_releases", "commerce_reports"),
}

# $ Advisory-lock scopes: `all` takes every per-source lock, so it conflicts with each one.$
LOCK_SCOPES: dict[str, tuple[str, ...]] = {
    "uae": ("uae",),
    "cba": ("cba",),
    "commerce": ("commerce",),
    "all": ("cba", "commerce", "uae"),
}


@dataclass(frozen=True, slots=True)
class CrawlOptions:
    """### Validated options for one triggered crawl."""

    source: str
    max_pages: int | None = None
    trigger_type: str = "api"


def _advisory_key(scope: str) -> int:
    """### Derive a stable signed 64-bit advisory-lock key for one scope."""
    digest = hashlib.sha256(f"lexcrawl:crawl:{scope}".encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _arguments(options: CrawlOptions) -> Namespace:
    """### Build the CLI-compatible argument namespace for the selected scope."""
    return Namespace(
        source=options.source,
        engine=None,
        headed=False,
        max_pages=options.max_pages,
        request_delay=None,
        timeout=None,
        max_retries=None,
        concurrency=None,
        no_robots=False,
        bilingual=False,
        bootstrap=False,
    )


def execute_scope(options: CrawlOptions, settings: Settings) -> CrawlRunReport:
    """### Build and run the selected actors synchronously (blocking; call in a worker)."""
    arguments = _arguments(options)
    actors = cli.build_actors(arguments, settings)
    selected = list(cli.SOURCE_NAMES) if options.source == "all" else [options.source]
    report = CrawlRunReport()
    fetchers: list[Fetcher] = []
    try:
        grouped: dict[str, list[Actor]] = {}
        for name, actor in zip(selected, actors, strict=True):
            grouped.setdefault(cli.engine_for_source(name, arguments, settings), []).append(actor)
        for engine, engine_actors in grouped.items():
            fetcher = cli.build_fetcher(arguments, settings, engine=engine)
            fetchers.append(fetcher)
            partial = run_crawl(engine_actors, fetcher)
            report.results.extend(partial.results)
            report.duration_seconds += partial.duration_seconds
    finally:
        for fetcher in fetchers:
            fetcher.close()
        for actor in actors:
            close = getattr(actor, "close", None)
            if callable(close):
                close()
    return report


def _release_locks(connection: Connection, keys: tuple[int, ...]) -> None:
    """### Release the advisory locks held on a connection."""
    try:
        for key in keys:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    except Exception:  # $ A dropped connection releases its session locks anyway.$
        LOGGER.warning("crawl_lock_release_failed")


class CrawlExecutor:
    """### Accept crawl triggers and run them off the event loop.

    Execution is **in-process** and not durable across application restarts; it is intended for
    a single application worker. Concurrency is enforced with PostgreSQL session advisory locks
    held on a dedicated connection for the duration of each crawl, so at most one execution per
    source-scope runs at a time and `all` conflicts with any individual source.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        engine: Engine,
        session_factory: sessionmaker[Session],
        runner: Callable[[CrawlOptions, Settings], CrawlRunReport] | None = None,
        max_workers: int | None = None,
    ) -> None:
        """### Configure the executor with its own engine, session factory, and worker pool."""
        self._settings = settings
        self._engine = engine
        self._session_factory = session_factory
        self._runner = runner
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers or len(cli.SOURCE_NAMES),
            thread_name_prefix="lexcrawl-crawl",
        )

    def start(self, options: CrawlOptions) -> UUID:
        """### Reserve locks, create the running run row, and submit the crawl to a worker."""
        keys = tuple(_advisory_key(scope) for scope in LOCK_SCOPES[options.source])
        lock_connection = self._engine.connect().execution_options(isolation_level="AUTOCOMMIT")
        acquired: list[int] = []
        try:
            for key in keys:
                locked = bool(lock_connection.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": key}).scalar())
                if not locked:
                    raise ConflictError(f"a crawl is already active for source {options.source!r}")
                acquired.append(key)
            with session_scope(self._session_factory) as session:
                run = create_run(session, trigger_type=options.trigger_type, started_at=datetime.now(tz=UTC))
                run_uuid = run.run_id
        except Exception:
            _release_locks(lock_connection, tuple(acquired))
            lock_connection.close()
            raise
        self._pool.submit(self._execute, run_uuid, options, lock_connection, tuple(acquired))
        return run_uuid

    def _execute(
        self,
        run_uuid: UUID,
        options: CrawlOptions,
        lock_connection: Connection,
        keys: tuple[int, ...],
    ) -> None:
        """### Run one crawl and persist it against its run, then release the locks."""
        try:
            runner = self._runner if self._runner is not None else execute_scope
            report = runner(options, self._settings)
            persist_crawl_report(self._session_factory, report, run_id=run_uuid)
        except Exception as error:
            LOGGER.exception("crawl_execution_failed", extra={"run_id": str(run_uuid)})
            try:
                persist_run_failure(
                    self._session_factory,
                    run_uuid,
                    source_codes=SOURCE_CODES[options.source],
                    message=str(error),
                )
            except Exception:
                LOGGER.exception("crawl_failure_not_recorded", extra={"run_id": str(run_uuid)})
        finally:
            _release_locks(lock_connection, keys)
            lock_connection.close()

    def shutdown(self) -> None:
        """### Stop accepting new work and cancel queued tasks."""
        self._pool.shutdown(wait=False, cancel_futures=True)
