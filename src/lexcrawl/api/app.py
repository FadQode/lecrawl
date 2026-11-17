"""### FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lexcrawl.api.routes import analytics, documents, health, jobs, runs, sources
from lexcrawl.config import Settings, get_settings
from lexcrawl.db.engine import create_db_engine
from lexcrawl.db.session import create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    """### Build the FastAPI application, opening the database pool at startup."""
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_db_engine(resolved) if resolved.database_url.strip() else None
        app.state.settings = resolved
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine) if engine is not None else None
        try:
            yield
        finally:
            if engine is not None:
                engine.dispose()

    app = FastAPI(title=resolved.api_title, version="0.1.0", lifespan=lifespan)
    for router in (health.router, sources.router, documents.router, runs.router, analytics.router, jobs.router):
        app.include_router(router)
    return app
