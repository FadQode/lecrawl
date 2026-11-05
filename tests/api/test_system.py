"""### API tests for health and readiness."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lexcrawl.api import create_app
from lexcrawl.config import Settings


def test_health_returns_ok(client: TestClient) -> None:
    """### The liveness probe reports the running version."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"]


def test_ready_reports_database_available(client: TestClient) -> None:
    """### The readiness probe confirms PostgreSQL is reachable."""
    response = client.get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok"}


def test_ready_returns_503_when_database_is_unreachable() -> None:
    """### Readiness fails with Problem Details when the database cannot be reached."""
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://lexcrawl:lexcrawl@localhost:59999/lexcrawl_test",
        db_connect_timeout=1,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 503
