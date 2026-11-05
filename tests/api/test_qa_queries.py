"""### QA: query efficiency (no N+1) against PostgreSQL."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_content


def count_selects(client: TestClient, url: str) -> tuple[int, list[str]]:
    """### Count SELECT statements issued while serving one request."""
    engine = client.app.state.engine  # type: ignore[attr-defined]
    statements: list[str] = []

    def before_cursor_execute(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        """### Record a SELECT statement."""
        if statement.lstrip().lower().startswith("select"):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        response = client.get(url)
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
    return response.status_code, statements


def test_article_list_uses_bounded_queries(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### The article list issues a bounded number of queries (count + rows)."""
    seed_content(api_session_factory)
    status, statements = count_selects(client, "/api/v1/articles")
    assert status == 200
    assert len(statements) <= 4


def test_run_list_uses_bounded_queries(client: TestClient, api_session_factory: sessionmaker[Session]) -> None:
    """### The crawl-run list eager-loads per-source results without N+1."""
    seed_content(api_session_factory)
    status, statements = count_selects(client, "/api/v1/crawl-runs")
    assert status == 200
    assert len(statements) <= 6
