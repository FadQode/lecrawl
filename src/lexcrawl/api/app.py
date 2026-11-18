"""### FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import Engine

from lexcrawl.api.background import QueueDrainer
from lexcrawl.api.routes import analytics, documents, health, jobs, metrics, runs, sources
from lexcrawl.config import Settings, get_settings
from lexcrawl.db.engine import create_db_engine, database_configured
from lexcrawl.db.session import create_session_factory
from lexcrawl.factory import ensure_registry_sources
from lexcrawl.logging import get_logger

LOGGER = get_logger(__name__)

EMBEDDED_WORKER_NAME = "api-embedded-worker"


def build_drainer(settings: Settings, engine: Engine) -> QueueDrainer:
    """### Build the in-process queue runner over the API's shared engine."""
    from lexcrawl.worker import run_once  # $ Imported lazily: the worker pulls in the crawl stack.$

    return QueueDrainer(lambda: run_once(settings, worker_name=EMBEDDED_WORKER_NAME, engine=engine))


def create_app(settings: Settings | None = None) -> FastAPI:
    """### Build the FastAPI application, opening the database pool at startup."""
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(resolved) if database_configured(resolved) else None
        app.state.settings = resolved
        app.state.engine = engine
        app.state.drainer = None
        if engine is not None:
            factory = create_session_factory(engine)
            try:
                # $ Register every registry source so reads and runs have a target row.$
                ensure_registry_sources(factory)
            except Exception:
                # $ A database that is not ready (or not migrated) must not stop the API from
                # $ starting: /healthz stays up and /readyz reports the problem.$
                LOGGER.exception("startup_source_seed_failed")
            app.state.session_factory = factory
            if resolved.embedded_worker:
                drainer = build_drainer(resolved, engine)
                app.state.drainer = drainer
                # $ Resume anything left queued by a previous container (restart or sleep).$
                drainer.kick()
        else:
            app.state.session_factory = None
        try:
            yield
        finally:
            if app.state.drainer is not None:
                app.state.drainer.stop()
            if engine is not None:
                engine.dispose()

    app = FastAPI(title=resolved.api_title, version="0.1.0", lifespan=lifespan)
    for router in (
        health.router,
        metrics.router,
        sources.router,
        documents.router,
        runs.router,
        analytics.router,
        jobs.router,
    ):
        app.include_router(router)
    return app
