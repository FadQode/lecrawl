"""### FastAPI dependencies: settings, engine, and session factory."""

from __future__ import annotations

from fastapi import HTTPException, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.config import Settings


def get_settings_dep(request: Request) -> Settings:
    """### Return the app settings."""
    return request.app.state.settings  # type: ignore[no-any-return]


def get_engine(request: Request) -> Engine | None:
    """### Return the shared engine (or None when unconfigured)."""
    return request.app.state.engine  # type: ignore[no-any-return]


def get_factory(request: Request) -> sessionmaker[Session]:
    """### Return the session factory, failing when the database is unconfigured."""
    factory = request.app.state.session_factory
    if factory is None:
        raise HTTPException(status_code=503, detail="database is not configured")
    return factory  # type: ignore[no-any-return]
