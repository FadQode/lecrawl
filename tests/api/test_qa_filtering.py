"""### QA: filtering behavior and classification stability."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_content


def test_unknown_source_returns_an_empty_set(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### An unknown source code is a valid filter that matches nothing."""
    seed_content(api_session_factory)
    body = client.get("/api/v1/articles", params={"source": "does-not-exist"}).json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0


def test_combined_filters(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### Source and date filters combine with AND semantics."""
    seed_content(api_session_factory)
    body = client.get(
        "/api/v1/articles",
        params={"source": "cba_news_releases", "published_from": "2025-01-01", "published_to": "2025-12-31"},
    ).json()
    assert body["pagination"]["total"] == 1
    assert body["items"][0]["source"] == "cba_news_releases"


def test_from_after_to_is_an_empty_set(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### A reversed date range is accepted and matches nothing (no error)."""
    seed_content(api_session_factory)
    body = client.get("/api/v1/articles", params={"published_from": "2027-01-01", "published_to": "2020-01-01"}).json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 0


def test_invalid_date_format_is_rejected(client: TestClient) -> None:
    """### A malformed date is a 422 validation failure."""
    assert client.get("/api/v1/articles", params={"published_to": "13-2019"}).status_code == 422


def test_filtering_does_not_change_classification(
    client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """### Filtering by a report source yields no articles, preserving classification."""
    seed_content(api_session_factory)
    body = client.get("/api/v1/articles", params={"source": "commerce_reports"}).json()
    assert body["pagination"]["total"] == 0


def test_date_filter_matches_year_precision_fallback(
    client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """### Year-precision items are matched by a same-year range via the year fallback."""
    seed_content(api_session_factory)
    body = client.get("/api/v1/articles", params={"published_from": "2026-01-01"}).json()
    assert [item["title"] for item in body["items"]] == ["UAE title"]
