"""### Composition root: DB-backed components plus the source registry re-exports.

Source-specific builders (checks, actors, scrapers, host allowlists) live in
``lexcrawl.sources``; this module keeps the process-wide persistence wiring and
re-exports the registry entry points so callers can keep importing them from ``factory``.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.checks.checkpoints import CheckpointStore, DbCheckpointStore
from lexcrawl.checks.identities import DocumentMemory
from lexcrawl.checks.run_records import DbRunRecorder, RunRecorder
from lexcrawl.config import Settings
from lexcrawl.crawling.base import Fetcher
from lexcrawl.crawling.http import HostPacer, HttpFetcher
from lexcrawl.db.engine import create_db_engine
from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.logging import get_logger
from lexcrawl.repositories.memory import DbDocumentMemory
from lexcrawl.repositories.sources import seed_sources
from lexcrawl.services.fetchers import HttpResourceFetcher
from lexcrawl.sources import (  # $ Re-exported for existing importers.$
    CHECK_SOURCES,
    REGISTRY,
    SOURCE_NAMES,
    build_actor,
    build_check,
    build_firecrawl,
    build_scraper,
    cba_sitemap_url,
    redirect_host_allowlist,
    uae_allowed_hosts,
)

LOGGER = get_logger(__name__)

# $ One pacer per process, so strategies in this process targeting a host share a cadence.$
_HOST_PACER: HostPacer | None = None


def shared_host_pacer() -> HostPacer:
    """### Return the process-wide per-host request pacer, creating it on first use."""
    global _HOST_PACER
    if _HOST_PACER is None:
        _HOST_PACER = HostPacer()
    return _HOST_PACER


def build_http_fetcher(settings: Settings) -> Fetcher:
    """### Build the plain HTTP strategy from settings, for CBA and the source checks."""
    return HttpFetcher(
        user_agent=settings.crawl_user_agent,
        timeout_seconds=settings.crawl_request_timeout_seconds,
        max_retries=settings.crawl_max_retries,
        backoff_seconds=settings.crawl_retry_backoff_seconds,
        request_delay_seconds=settings.crawl_request_delay_seconds,
        max_response_bytes=settings.crawl_max_response_bytes,
        max_cache_entries=settings.crawl_cache_max_entries,
        allowed_redirect_hosts=redirect_host_allowlist(settings),
        allowed_hosts=redirect_host_allowlist(settings),
        host_pacer=shared_host_pacer(),
    )


def build_resource_fetcher(settings: Settings) -> HttpFetcher:
    """### Build an HTTP fetcher for attachments (host suffixes, no in-memory cache)."""
    return HttpFetcher(
        user_agent=settings.crawl_user_agent,
        timeout_seconds=settings.crawl_request_timeout_seconds,
        max_retries=settings.crawl_max_retries,
        backoff_seconds=settings.crawl_retry_backoff_seconds,
        request_delay_seconds=settings.crawl_request_delay_seconds,
        max_response_bytes=settings.resource_max_bytes,
        allowed_suffixes=tuple(settings.resource_allowed_host_suffixes),
        enable_cache=False,
        host_pacer=shared_host_pacer(),
    )


def build_document_memory(settings: Settings, *, engine: Engine | None = None) -> DocumentMemory:
    """### Build the identity memory, optionally sharing an injected engine."""
    if engine is not None:
        owns_engine = False
    else:
        engine = create_db_engine(settings)
        owns_engine = True
    return DbDocumentMemory(
        engine,
        fetcher=HttpResourceFetcher(build_resource_fetcher(settings)),
        attempt_cap=settings.resource_extraction_attempts,
        max_bytes=settings.resource_max_bytes,
        lease_seconds=settings.resource_claim_lease_seconds,
        document_attempt_cap=settings.document_extraction_attempts,
        owns_engine=owns_engine,
    )


@dataclass(slots=True)
class Runtime:
    """### Process-wide persistence components sharing one engine."""

    engine: Engine
    memory: DocumentMemory
    checkpoints: CheckpointStore
    recorder: RunRecorder
    owns_engine: bool = False

    def close(self) -> None:
        """### Close components (no-op for borrowed engines), then dispose if owned."""
        for component in (self.memory, self.checkpoints, self.recorder):
            component.close()
        if self.owns_engine:
            self.engine.dispose()


def build_runtime(settings: Settings, *, engine: Engine | None = None) -> Runtime:
    """### Build memory/checkpoints/recorder over one shared engine."""
    owns_engine = engine is None
    if engine is None:
        engine = create_db_engine(settings)
    _seed_registry_sources(engine)
    return Runtime(
        engine=engine,
        memory=build_document_memory(settings, engine=engine),
        checkpoints=build_checkpoint_store(settings, engine=engine),
        recorder=build_run_recorder(settings, engine=engine),
        owns_engine=owns_engine,
    )


def ensure_registry_sources(factory: sessionmaker[Session]) -> int:
    """### Seed every registered source into the sources table (idempotent)."""
    rows = [(spec.code, spec.display_name, spec.base_url) for spec in REGISTRY.values()]
    with session_scope(factory) as session:
        return seed_sources(session, rows)


def _seed_registry_sources(engine: Engine) -> None:
    """### Best-effort source seeding so a failed seed never blocks building the runtime."""
    try:
        ensure_registry_sources(create_session_factory(engine))
    except Exception:
        # $ A run still fails loudly later if a source is genuinely missing.$
        LOGGER.warning("source_seed_failed")


def build_run_recorder(settings: Settings, *, engine: Engine | None = None) -> RunRecorder:
    """### Build the crawl-run recorder, optionally sharing an injected engine."""
    if engine is not None:
        return DbRunRecorder(engine, owns_engine=False)
    return DbRunRecorder(create_db_engine(settings))


def build_checkpoint_store(settings: Settings, *, engine: Engine | None = None) -> CheckpointStore:
    """### Build the checkpoint store, optionally sharing an injected engine."""
    if engine is not None:
        return DbCheckpointStore(engine, owns_engine=False)
    return DbCheckpointStore(create_db_engine(settings))


__all__ = [
    "CHECK_SOURCES",
    "SOURCE_NAMES",
    "Runtime",
    "build_actor",
    "build_check",
    "build_checkpoint_store",
    "build_document_memory",
    "build_firecrawl",
    "build_http_fetcher",
    "build_resource_fetcher",
    "build_run_recorder",
    "build_runtime",
    "build_scraper",
    "cba_sitemap_url",
    "ensure_registry_sources",
    "redirect_host_allowlist",
    "shared_host_pacer",
    "uae_allowed_hosts",
]
