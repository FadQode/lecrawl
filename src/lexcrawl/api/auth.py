"""### Bearer-token authentication for job submission."""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, Request


def require_bearer_token(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    """### Require a valid bearer token, comparing in constant time."""
    expected = request.app.state.settings.api_token
    if not expected:
        raise HTTPException(status_code=503, detail="API authentication is not configured (API_TOKEN unset)")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="invalid bearer token")
