"""### API tests for report endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_content


def test_list_reports_excludes_articles(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### Only reports are listed under the reports route."""
    seed_content(api_session_factory)
    response = client.get("/api/v1/reports")
    body = response.json()
    assert response.status_code == 200
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["source"] == "commerce_reports"
    assert body["items"][0]["kind"] == "report"


def test_report_detail_and_resources(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### The report detail returns content and an (empty) resource list."""
    seed_content(api_session_factory)
    report_id = client.get("/api/v1/reports").json()["items"][0]["id"]
    detail = client.get(f"/api/v1/reports/{report_id}")
    assert detail.status_code == 200
    assert detail.json()["content"].startswith("TEXT-BEGIN")
    resources = client.get(f"/api/v1/reports/{report_id}/resources")
    assert resources.status_code == 200
    assert resources.json() == []


def test_article_id_under_reports_is_not_found(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### An article identifier is not retrievable through the reports route."""
    seed_content(api_session_factory)
    listing = client.get("/api/v1/articles", params={"source": "uae_legislation_news"}).json()
    article_id = listing["items"][0]["id"]
    assert client.get(f"/api/v1/reports/{article_id}").status_code == 404


def test_missing_report_is_a_problem_details_404(client: TestClient) -> None:
    """### A missing report id returns Problem Details."""
    response = client.get("/api/v1/reports/999999")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
