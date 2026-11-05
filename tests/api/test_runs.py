"""### API tests for crawl-run history and errors."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import persist_results
from tests.integration.db.builders import SITE, document, result


def seed_run(factory: sessionmaker[Session]) -> None:
    """### Persist one CBA run that produced a document and a bounded-pagination error."""
    persist_results(
        factory,
        result(
            source="cba_news_releases",
            documents=(
                document(
                    source="cba_news_releases",
                    url=f"{SITE}/article/two",
                    native_id="cba-2",
                    title="CBA title",
                    language="en",
                    type_of_law="News Releases",
                    published_at=date(2025, 11, 3),
                ),
            ),
            errors=("stopped after hitting max_pages=1",),
        ),
    )


def test_list_runs(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### A persisted run is listed with its per-source results."""
    seed_run(api_session_factory)
    response = client.get("/api/v1/crawl-runs")
    body = response.json()
    assert response.status_code == 200
    assert body["pagination"]["total"] == 1
    run = body["items"][0]
    assert run["trigger_type"] == "manual"
    assert run["sources"][0]["source"] == "cba_news_releases"
    assert run["sources"][0]["documents_processed"] == 1


def test_get_run_and_errors(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### A run is retrievable by id and its errors are classified."""
    seed_run(api_session_factory)
    run_id = client.get("/api/v1/crawl-runs").json()["items"][0]["run_id"]
    detail = client.get(f"/api/v1/crawl-runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["run_id"] == run_id
    errors = client.get(f"/api/v1/crawl-runs/{run_id}/errors")
    assert errors.status_code == 200
    body = errors.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["stage"] == "discovery"
    assert body["items"][0]["error_type"] == "budget_error"


def test_run_filters(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### Runs can be filtered by source and status."""
    seed_run(api_session_factory)
    by_source = client.get("/api/v1/crawl-runs", params={"source": "cba_news_releases"}).json()
    assert by_source["pagination"]["total"] == 1
    other_source = client.get("/api/v1/crawl-runs", params={"source": "commerce_reports"}).json()
    assert other_source["pagination"]["total"] == 0
    by_status = client.get("/api/v1/crawl-runs", params={"status": "partial_failure"}).json()
    assert by_status["pagination"]["total"] == 1


def test_unknown_run_and_invalid_uuid(client: TestClient) -> None:
    """### Unknown runs are 404 and malformed identifiers are 422."""
    assert client.get("/api/v1/crawl-runs/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/v1/crawl-runs/not-a-uuid").status_code == 422
    assert client.get("/api/v1/crawl-runs/00000000-0000-0000-0000-000000000000/errors").status_code == 404
