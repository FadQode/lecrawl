"""### QA: pagination boundaries, ordering, and totals."""

from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import persist_results
from tests.integration.db.builders import SITE, document, result


def seed_many(factory: sessionmaker[Session], count: int = 5) -> None:
    """### Persist `count` dated articles under one source."""
    documents = tuple(
        document(
            source="uae_legislation_news",
            url=f"{SITE}/ar/news/{index}",
            native_id=f"n{index}",
            title=f"T{index}",
            language="ar",
            published_at=date(2026, 1, index + 1),
        )
        for index in range(count)
    )
    persist_results(factory, result(source="uae_legislation_news", documents=documents))


def test_default_limit_and_offset(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### Defaults are the configured page size and offset zero."""
    seed_many(api_session_factory)
    body = client.get("/api/v1/articles").json()
    assert body["pagination"] == {"limit": 25, "offset": 0, "total": 5}


def test_minimum_limit(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### The smallest allowed limit returns one item."""
    seed_many(api_session_factory)
    body = client.get("/api/v1/articles", params={"limit": 1}).json()
    assert body["pagination"]["limit"] == 1
    assert len(body["items"]) == 1


def test_limit_above_max_is_clamped_to_the_configured_maximum(
    client: TestClient, api_session_factory: sessionmaker[Session]
) -> None:
    """### A limit above api_page_size_max (200) is clamped, not rejected."""
    seed_many(api_session_factory)
    body = client.get("/api/v1/articles", params={"limit": 100000}).json()
    assert body["pagination"]["limit"] == 200


def test_negative_limit_and_offset_are_rejected(client: TestClient) -> None:
    """### Negative pagination values are a 422 validation failure."""
    assert client.get("/api/v1/articles", params={"limit": -1}).status_code == 422
    assert client.get("/api/v1/articles", params={"offset": -1}).status_code == 422


def test_offset_beyond_records(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### An offset beyond the result set returns no items but the true total."""
    seed_many(api_session_factory, 2)
    body = client.get("/api/v1/articles", params={"offset": 100}).json()
    assert body["items"] == []
    assert body["pagination"]["total"] == 2


def test_pages_are_disjoint_and_descending(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### Pages do not overlap and ordering is publication date descending."""
    seed_many(api_session_factory, 5)
    ids: list[int] = []
    for offset in range(0, 5, 2):
        page = client.get("/api/v1/articles", params={"limit": 2, "offset": offset}).json()
        ids.extend(item["id"] for item in page["items"])
    assert len(ids) == 5
    assert len(set(ids)) == 5
    titles = [item["title"] for item in client.get("/api/v1/articles", params={"limit": 5}).json()["items"]]
    assert titles == ["T4", "T3", "T2", "T1", "T0"]


def test_null_publication_date_sorts_last(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### Items without an exact date order after dated items (NULLS LAST)."""
    persist_results(
        api_session_factory,
        result(
            source="uae_legislation_news",
            documents=(
                document(
                    source="uae_legislation_news",
                    url=f"{SITE}/ar/news/dated",
                    native_id="dated",
                    title="Dated",
                    language="ar",
                    published_at=date(2026, 2, 1),
                ),
                document(
                    source="uae_legislation_news",
                    url=f"{SITE}/ar/news/undated",
                    native_id="undated",
                    title="Undated",
                    language="ar",
                    published_at=None,
                    published_year=2024,
                    published_precision="year",
                ),
            ),
        ),
    )
    titles = [item["title"] for item in client.get("/api/v1/articles").json()["items"]]
    assert titles == ["Dated", "Undated"]


def test_exactly_one_page_and_multiple_pages(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### A single full page and a two-page result set both paginate correctly."""
    seed_many(api_session_factory, 3)
    one_page = client.get("/api/v1/articles", params={"limit": 3}).json()
    assert len(one_page["items"]) == 3
    first = client.get("/api/v1/articles", params={"limit": 2, "offset": 0}).json()
    second = client.get("/api/v1/articles", params={"limit": 2, "offset": 2}).json()
    assert len(first["items"]) == 2
    assert len(second["items"]) == 1
