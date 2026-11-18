"""### FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lexcrawl.api.routes import analytics, documents, health, jobs, metrics, runs, sources
from lexcrawl.config import Settings, get_settings
from lexcrawl.db.engine import create_db_engine, database_configured
from lexcrawl.db.session import create_session_factory
from lexcrawl.factory import ensure_registry_sources


def create_app(settings: Settings | None = None) -> FastAPI:
    """### Build the FastAPI application, opening the database pool at startup."""
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(resolved) if database_configured(resolved) else None
        app.state.settings = resolved
        app.state.engine = engine
        if engine is not None:
            factory = create_session_factory(engine)
            # $ Register every registry source so reads and runs have a target row.$
            ensure_registry_sources(factory)
            app.state.session_factory = factory
        else:
            app.state.session_factory = None
        try:
            yield
        finally:
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
