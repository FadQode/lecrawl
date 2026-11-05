"""### RFC 9457 Problem Details error handling for the content API."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from lexcrawl.logging import get_logger

PROBLEM_CONTENT_TYPE = "application/problem+json"
_TITLES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    409: "Conflict",
    422: "Unprocessable Entity",
    500: "Internal Server Error",
    503: "Service Unavailable",
}

LOGGER = get_logger(__name__)


class NotFoundError(Exception):
    """### Raised when a requested content item does not exist."""

    def __init__(self, detail: str) -> None:
        """### Record the operator-safe detail message."""
        super().__init__(detail)
        self.detail = detail


class ConflictError(Exception):
    """### Raised when a conflicting operation (e.g. an active crawl) is already in progress."""

    def __init__(self, detail: str) -> None:
        """### Record the operator-safe detail message."""
        super().__init__(detail)
        self.detail = detail


class UnauthorizedError(Exception):
    """### Raised when a trigger request lacks valid credentials."""

    def __init__(self, detail: str) -> None:
        """### Record the operator-safe detail message."""
        super().__init__(detail)
        self.detail = detail


class DependencyUnavailableError(Exception):
    """### Raised when a required dependency such as PostgreSQL is unavailable."""

    def __init__(self, detail: str) -> None:
        """### Record the operator-safe detail message."""
        super().__init__(detail)
        self.detail = detail


def problem_response(
    *,
    request: Request,
    status_code: int,
    title: str,
    detail: str | None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """### Build a Problem Details JSON response without leaking internals."""
    return JSONResponse(
        status_code=status_code,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers,
        content={
            "type": "about:blank",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": request.url.path,
        },
    )


def _validation_detail(error: RequestValidationError) -> str:
    """### Summarize validation errors without exposing internals."""
    parts: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ()) if part != "body")
        message = item.get("msg", "invalid value")
        parts.append(f"{location}: {message}" if location else str(message))
    return "; ".join(parts) or "request validation failed"


def register_exception_handlers(app: FastAPI) -> None:
    """### Register the Problem Details handlers on the application."""

    @app.exception_handler(NotFoundError)
    async def _handle_not_found(request: Request, error: NotFoundError) -> JSONResponse:
        """### Render a missing content item as 404."""
        return problem_response(request=request, status_code=404, title=_TITLES[404], detail=error.detail)

    @app.exception_handler(ConflictError)
    async def _handle_conflict(request: Request, error: ConflictError) -> JSONResponse:
        """### Render a conflicting operation as 409."""
        return problem_response(request=request, status_code=409, title=_TITLES[409], detail=error.detail)

    @app.exception_handler(UnauthorizedError)
    async def _handle_unauthorized(request: Request, error: UnauthorizedError) -> JSONResponse:
        """### Render missing or invalid credentials as 401."""
        return problem_response(
            request=request,
            status_code=401,
            title=_TITLES[401],
            detail=error.detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(DependencyUnavailableError)
    async def _handle_unavailable(request: Request, error: DependencyUnavailableError) -> JSONResponse:
        """### Render a missing dependency as 503."""
        return problem_response(request=request, status_code=503, title=_TITLES[503], detail=error.detail)

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, error: RequestValidationError) -> JSONResponse:
        """### Render request validation failures as 422 without internals."""
        return problem_response(request=request, status_code=422, title=_TITLES[422], detail=_validation_detail(error))

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, error: StarletteHTTPException) -> JSONResponse:
        """### Render raised HTTP errors as Problem Details."""
        title = _TITLES.get(error.status_code, "Error")
        return problem_response(request=request, status_code=error.status_code, title=title, detail=str(error.detail))

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, error: Exception) -> JSONResponse:
        """### Render an unexpected failure as 500 without exposing the cause."""
        LOGGER.exception("api_unhandled_error", extra={"path": request.url.path})
        return problem_response(
            request=request,
            status_code=500,
            title=_TITLES[500],
            detail="An unexpected error occurred while processing the request.",
        )
