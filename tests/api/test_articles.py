"""### API tests for article endpoints and classification."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.actors.base import ActorRunResult
from tests.api.conftest import persist_results, seed_content


def test_list_articles_excludes_reports(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### Only articles are listed, and the body is not in the summary."""
    seed_content(api_session_factory)
    response = client.get("/api/v1/articles")
    assert response.status_code == 200
    body = response.json()
    assert body["pagination"]["total"] == 2
    assert {item["source"] for item in body["items"]} == {"uae_legislation_news", "cba_news_releases"}
    assert all(item["kind"] == "article" for item in body["items"])
    assert all("content" not in item for item in body["items"])


def test_filter_articles_by_source(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### The source filter narrows the result set."""
    seed_content(api_session_factory)
    response = client.get("/api/v1/articles", params={"source": "cba_news_releases"})
    body = response.json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["source"] == "cba_news_releases"


def test_filter_articles_by_publication_date(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### The publication date filter is inclusive."""
    seed_content(api_session_factory)
    response = client.get("/api/v1/articles", params={"published_from": "2026-01-01"})
    body = response.json()
    assert [item["title"] for item in body["items"]] == ["UAE title"]


def test_article_pagination_boundaries(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### Pagination slices deterministically and reports the full total."""
    seed_content(api_session_factory)
    first = client.get("/api/v1/articles", params={"limit": 1, "offset": 0}).json()
    second = client.get("/api/v1/articles", params={"limit": 1, "offset": 1}).json()
    empty = client.get("/api/v1/articles", params={"limit": 1, "offset": 9}).json()
    assert first["pagination"] == {"limit": 1, "offset": 0, "total": 2}
    assert len(first["items"]) == 1
    assert first["items"][0]["id"] != second["items"][0]["id"]
    assert empty["items"] == []
    assert empty["pagination"]["total"] == 2


def test_empty_result_set(client: TestClient) -> None:
    """### An empty result set returns zero items and a zero total."""
    response = client.get("/api/v1/articles")
    body = response.json()
    assert response.status_code == 200
    assert body["items"] == []
    assert body["pagination"]["total"] == 0


def test_article_detail_serializes_content_and_localizations(
    client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """### The detail endpoint returns the marked body, localizations, and resources."""
    seed_content(api_session_factory)
    listing = client.get("/api/v1/articles", params={"source": "uae_legislation_news"}).json()
    article_id = listing["items"][0]["id"]
    response = client.get(f"/api/v1/articles/{article_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["content"].startswith("TEXT-BEGIN")
    assert body["language"] == "ar"
    assert len(body["localizations"]) == 1
    assert body["localizations"][0]["has_content"] is True
    assert len(body["resources"]) == 1
    assert body["resources"][0]["resource_type"] == "pdf"


def test_report_id_under_articles_is_not_found(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### A report identifier is not retrievable through the articles route."""
    seed_content(api_session_factory)
    report_id = client.get("/api/v1/reports").json()["items"][0]["id"]
    assert client.get(f"/api/v1/articles/{report_id}").status_code == 404


def test_article_resources_endpoint(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### The nested resources route returns the parent reference."""
    seed_content(api_session_factory)
    listing = client.get("/api/v1/articles", params={"source": "uae_legislation_news"}).json()
    article_id = listing["items"][0]["id"]
    response = client.get(f"/api/v1/articles/{article_id}/resources")
    assert response.status_code == 200
    resources = response.json()
    assert len(resources) == 1
    assert resources[0]["parent"]["kind"] == "article"
    assert resources[0]["parent"]["id"] == article_id


def test_invalid_query_parameters_return_422(client: TestClient) -> None:
    """### Invalid pagination and date filters are rejected as 422."""
    assert client.get("/api/v1/articles", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/articles", params={"offset": -1}).status_code == 422
    assert client.get("/api/v1/articles", params={"published_from": "not-a-date"}).status_code == 422


def test_missing_article_is_a_problem_details_404(client: TestClient) -> None:
    """### A missing article id returns Problem Details."""
    response = client.get("/api/v1/articles/999999")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")


def test_persisted_run_is_not_an_article(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### A run with an empty result still yields no articles."""
    persist_results(api_session_factory, ActorRunResult(source="commerce_reports"))
    assert client.get("/api/v1/articles").json()["pagination"]["total"] == 0
