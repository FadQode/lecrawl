"""### QA: OpenAPI document, Swagger UI, and ReDoc."""

from __future__ import annotations

from fastapi.testclient import TestClient

EXPECTED_PATHS = {
    "/api/v1/health",
    "/api/v1/ready",
    "/api/v1/sources",
    "/api/v1/sources/{source_code}",
    "/api/v1/articles",
    "/api/v1/articles/{article_id}",
    "/api/v1/articles/{article_id}/resources",
    "/api/v1/reports",
    "/api/v1/reports/{report_id}",
    "/api/v1/reports/{report_id}/resources",
    "/api/v1/resources",
    "/api/v1/resources/{resource_id}",
    "/api/v1/crawl-runs",
    "/api/v1/crawl-runs/{run_id}",
    "/api/v1/crawl-runs/{run_id}/errors",
}


def test_openapi_document_metadata_and_operations(client: TestClient) -> None:
    """### The OpenAPI document is valid, complete, and consistently annotated."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert schema["openapi"].startswith("3.")
    info = schema["info"]
    assert info["title"] == "LexCrawl API"
    assert info["version"]
    assert info["description"]
    assert set(schema["paths"]) >= EXPECTED_PATHS

    tag_names = {tag["name"] for tag in schema["tags"]}
    assert {"System", "Sources", "Articles", "Reports", "Resources", "Crawl Runs"} <= tag_names

    operation_ids: list[str] = []
    for item in schema["paths"].values():
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            operation_ids.append(operation["operationId"])
            assert operation.get("summary")
            assert operation.get("tags")
    assert len(operation_ids) == len(set(operation_ids))
    assert len(operation_ids) == 16


def test_error_responses_are_documented(client: TestClient) -> None:
    """### Error responses use the Problem Details media type in the document."""
    schema = client.get("/openapi.json").json()
    detail_responses = schema["paths"]["/api/v1/articles/{article_id}"]["get"]["responses"]
    assert "404" in detail_responses
    assert "422" in detail_responses
    assert "application/problem+json" in detail_responses["404"]["content"]

    ready_responses = schema["paths"]["/api/v1/ready"]["get"]["responses"]
    assert "503" in ready_responses


def test_swagger_ui_and_redoc_render(client: TestClient) -> None:
    """### Swagger UI and ReDoc render and are not disabled."""
    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_pagination_total_is_documented(client: TestClient) -> None:
    """### Pagination fields carry descriptions in the schema."""
    schema = client.get("/openapi.json").json()
    pagination = schema["components"]["schemas"]["Pagination"]
    assert set(pagination["properties"]) == {"limit", "offset", "total"}
    assert pagination["properties"]["total"]["description"]
