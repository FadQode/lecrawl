"""### FastAPI application factory for the LexCrawl content API."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from lexcrawl import __version__
from lexcrawl.api.exceptions import register_exception_handlers
from lexcrawl.api.openapi import DESCRIPTION, TAGS, clean_docstring_markers, operation_id
from lexcrawl.api.routers import articles, reports, resources, runs, sources, system
from lexcrawl.api.services.crawl_execution import CrawlExecutor
from lexcrawl.config import Settings, get_settings
from lexcrawl.db.engine import create_db_engine
from lexcrawl.db.session import create_session_factory
from lexcrawl.logging import configure_logging, get_logger
from lexcrawl.scheduler import CrawlScheduler

API_PREFIX = "/api/v1"
LOGGER = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """### Create the database engine and crawl services on startup, dispose them on shutdown."""
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, settings.log_format)
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.crawl_executor = CrawlExecutor(settings=settings, engine=engine, session_factory=factory)
    app.state.crawl_scheduler = CrawlScheduler(executor=app.state.crawl_executor, settings=settings)
    app.state.crawl_scheduler.start()
    LOGGER.info("api_started", extra={"environment": settings.environment})
    try:
        yield
    finally:
        app.state.crawl_scheduler.shutdown()
        app.state.crawl_executor.shutdown()
        engine.dispose()
        LOGGER.info("api_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """### Build the FastAPI application with lifespan, handlers, and routers."""
    resolved = settings or get_settings()
    app = FastAPI(
        title="LexCrawl API",
        description=DESCRIPTION,
        version=__version__,
        openapi_tags=TAGS,
        lifespan=lifespan,
        generate_unique_id_function=operation_id,
    )
    app.state.settings = resolved
    register_exception_handlers(app)
    for module in (system, sources, articles, reports, resources, runs):
        app.include_router(module.router, prefix=API_PREFIX)

    default_openapi = app.openapi

    def custom_openapi() -> dict[str, Any]:
        """### Serve an OpenAPI document with library-generated markers removed."""
        if app.openapi_schema is None:
            app.openapi_schema = clean_docstring_markers(default_openapi())
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
    return app
