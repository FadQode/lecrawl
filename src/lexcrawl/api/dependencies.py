"""### Request-scoped dependencies for the content API."""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Query, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.api.exceptions import DependencyUnavailableError, UnauthorizedError
from lexcrawl.api.services.crawl_execution import CrawlExecutor
from lexcrawl.config import Settings


def get_settings(request: Request) -> Settings:
    """### Return the settings bound to the running application."""
    return cast(Settings, request.app.state.settings)


def get_session_factory(request: Request) -> sessionmaker[Session]:
    """### Return the application session factory created during startup."""
    return cast("sessionmaker[Session]", request.app.state.session_factory)


def get_session(factory: Annotated[sessionmaker[Session], Depends(get_session_factory)]) -> Iterator[Session]:
    """### Provide a request-scoped session that rolls back on error and always closes."""
    session = factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass(frozen=True, slots=True)
class PageParams:
    """### Validated pagination inputs for a list endpoint."""

    limit: int
    offset: int


def get_page_params(
    settings: Annotated[Settings, Depends(get_settings)],
    limit: Annotated[int | None, Query(ge=1, description="Maximum items to return")] = None,
    offset: Annotated[int, Query(ge=0, description="Number of items to skip")] = 0,
) -> PageParams:
    """### Resolve and bound the requested page size against configured limits."""
    resolved = settings.api_page_size_default if limit is None else limit
    if resolved > settings.api_page_size_max:
        resolved = settings.api_page_size_max
    return PageParams(limit=resolved, offset=offset)


SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]
PageParamsDep = Annotated[PageParams, Depends(get_page_params)]

_bearer = HTTPBearer(auto_error=False, description="Bearer token set via MANUAL_TRIGGER_TOKEN.")


def require_trigger_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(_bearer)],
    settings: SettingsDep,
) -> None:
    """### Require the configured bearer token for crawl-trigger requests."""
    expected = settings.manual_trigger_token
    if not expected.strip():
        # $ Triggering is disabled until an operator configures a token.$
        raise DependencyUnavailableError("crawl triggering is not configured")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError("missing bearer token")
    if not secrets.compare_digest(credentials.credentials, expected):
        raise UnauthorizedError("invalid bearer token")


def get_crawl_executor(request: Request) -> CrawlExecutor:
    """### Return the application's background crawl executor."""
    return cast(CrawlExecutor, request.app.state.crawl_executor)


CrawlExecutorDep = Annotated[CrawlExecutor, Depends(get_crawl_executor)]
