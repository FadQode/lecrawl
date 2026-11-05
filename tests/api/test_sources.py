"""### API tests for the source endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_sources_returns_the_registry(client: TestClient) -> None:
    """### Every configured source is listed."""
    response = client.get("/api/v1/sources")
    assert response.status_code == 200
    codes = {item["code"] for item in response.json()}
    assert codes == {"uae_legislation_news", "cba_news_releases", "commerce_reports"}


def test_get_source_returns_public_metadata(client: TestClient) -> None:
    """### A single source exposes identity, name, website, and active status."""
    response = client.get("/api/v1/sources/cba_news_releases")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Canadian Bar Association"
    assert body["is_active"] is True
    assert "base_url" in body
    assert "password" not in response.text.lower()


def test_unknown_source_is_a_problem_details_404(client: TestClient) -> None:
    """### An unknown source code returns an RFC 9457 Problem Details body."""
    response = client.get("/api/v1/sources/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["status"] == 404
    assert body["title"] == "Not Found"
