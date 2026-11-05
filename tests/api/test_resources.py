"""### API tests for resource endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_content


def test_list_resources_includes_parent(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### Resources are listed with a reference to their parent content."""
    seed_content(api_session_factory)
    response = client.get("/api/v1/resources")
    body = response.json()
    assert response.status_code == 200
    assert body["pagination"]["total"] == 1
    item = body["items"][0]
    assert item["resource_type"] == "pdf"
    assert item["extraction_status"] == "complete"
    assert item["has_content"] is True
    assert item["parent"]["kind"] == "article"


def test_resource_filters(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### Resource type and status filters narrow the result set."""
    seed_content(api_session_factory)
    matched = client.get("/api/v1/resources", params={"resource_type": "pdf", "extraction_status": "complete"}).json()
    assert matched["pagination"]["total"] == 1
    none = client.get("/api/v1/resources", params={"resource_type": "excel"}).json()
    assert none["pagination"]["total"] == 0


def test_filter_resources_by_document(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### Resources can be filtered by owning content id."""
    seed_content(api_session_factory)
    item = client.get("/api/v1/resources").json()["items"][0]
    by_document = client.get("/api/v1/resources", params={"document_id": item["document_id"]}).json()
    assert by_document["pagination"]["total"] == 1


def test_get_resource_and_not_found(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### A single resource is retrievable; an unknown id is a Problem Details 404."""
    seed_content(api_session_factory)
    resource_id = client.get("/api/v1/resources").json()["items"][0]["id"]
    response = client.get(f"/api/v1/resources/{resource_id}")
    assert response.status_code == 200
    assert response.json()["id"] == resource_id
    missing = client.get("/api/v1/resources/999999")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
