"""### API tests for error response formatting."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lexcrawl.api import create_app
from lexcrawl.config import Settings


def test_validation_error_is_problem_details(client: TestClient) -> None:
    """### Request validation failures use the Problem Details media type."""
    response = client.get("/api/v1/articles", params={"limit": 0})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 422
    assert body["title"] == "Unprocessable Entity"


def test_unhandled_exception_is_a_generic_500_without_leaks() -> None:
    """### An unexpected failure returns a generic 500 that never leaks details."""
    app = create_app(Settings(_env_file=None))

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("postgresql://user:secret@host/db")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 500
    assert "secret" not in response.text
    assert "Traceback" not in response.text
