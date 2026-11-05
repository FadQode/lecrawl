"""### API tests for crawl triggering: auth, validation, lifecycle, concurrency, failures."""

from __future__ import annotations

import threading
import time
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.actors.base import ActorRunResult
from lexcrawl.db.models import CrawlError, CrawlRun, CrawlRunSource, Document
from lexcrawl.orchestrator import CrawlRunReport
from tests.api.conftest import TRIGGER_TOKEN
from tests.integration.db.builders import SITE, document, result

HEADERS = {"Authorization": f"Bearer {TRIGGER_TOKEN}"}
RUNNER_PATH = "lexcrawl.api.services.crawl_execution.execute_scope"


def wait_for_run(client: TestClient, run_id: str, *, timeout: float = 10.0) -> dict[str, object]:
    """### Poll a run until it leaves the running state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/api/v1/crawl-runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError("crawl did not finish within the timeout")


def success_report() -> CrawlRunReport:
    """### Build a report that persists one article successfully."""
    return CrawlRunReport(
        results=[
            result(
                source="cba_news_releases",
                documents=(
                    document(
                        source="cba_news_releases",
                        url=f"{SITE}/article/x",
                        native_id="x",
                        title="Triggered",
                        language="en",
                        type_of_law="News Releases",
                        published_at=date(2025, 1, 1),
                    ),
                ),
                duration_seconds=0.1,
            )
        ]
    )


def fixed_runner(report: CrawlRunReport):
    """### Return a runner that always yields the given report."""

    def runner(options: object, settings: object) -> CrawlRunReport:
        return report

    return runner


def run_count(factory: sessionmaker[Session]) -> int:
    """### Count crawl-run rows in the test database."""
    with factory() as session:
        return int(session.execute(select(func.count()).select_from(CrawlRun)).scalar_one())


# $ Authentication$


def test_missing_token_is_401(client: TestClient) -> None:
    """### A trigger without Authorization is rejected with 401."""
    response = client.post("/api/v1/crawl-runs", json={"source": "cba"})
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "WWW-Authenticate" in response.headers


def test_invalid_token_is_401(client: TestClient) -> None:
    """### A wrong bearer token is rejected with 401."""
    response = client.post(
        "/api/v1/crawl-runs",
        json={"source": "cba"},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_unconfigured_token_is_503(unconfigured_client: TestClient) -> None:
    """### When no trigger token is configured the endpoint reports 503."""
    response = unconfigured_client.post("/api/v1/crawl-runs", json={"source": "cba"}, headers=HEADERS)
    assert response.status_code == 503


# $ Validation$


def test_invalid_source_is_422(client: TestClient, monkeypatch) -> None:
    """### An unsupported source value fails validation before any crawl starts."""
    monkeypatch.setattr(RUNNER_PATH, lambda options, settings: (_ for _ in ()).throw(AssertionError("ran")))
    response = client.post("/api/v1/crawl-runs", json={"source": "nope"}, headers=HEADERS)
    assert response.status_code == 422


def test_invalid_max_pages_is_422(client: TestClient, monkeypatch) -> None:
    """### A non-positive max_pages fails validation."""
    monkeypatch.setattr(RUNNER_PATH, lambda options, settings: (_ for _ in ()).throw(AssertionError("ran")))
    assert client.post("/api/v1/crawl-runs", json={"source": "cba", "max_pages": 0}, headers=HEADERS).status_code == 422


# $ Lifecycle$


def test_trigger_returns_202_and_completes(
    client: TestClient, api_session_factory: sessionmaker[Session], monkeypatch
) -> None:
    """### A valid trigger returns 202, one run row, and a stable run id through completion."""
    monkeypatch.setattr(RUNNER_PATH, fixed_runner(success_report()))
    response = client.post("/api/v1/crawl-runs", json={"source": "cba", "max_pages": 2}, headers=HEADERS)
    assert response.status_code == 202
    accepted = response.json()
    assert accepted["status"] == "running"
    assert accepted["trigger_type"] == "api"
    assert accepted["message"] == "Crawl accepted."
    run_id = accepted["id"]

    final = wait_for_run(client, run_id)
    assert final["run_id"] == run_id
    assert final["trigger_type"] == "api"
    assert final["status"] == "success"
    assert final["sources"][0]["source"] == "cba_news_releases"

    assert run_count(api_session_factory) == 1
    listing = client.get("/api/v1/crawl-runs").json()
    assert listing["pagination"]["total"] == 1
    assert listing["items"][0]["run_id"] == run_id
    with api_session_factory() as session:
        assert int(session.execute(select(func.count()).select_from(Document)).scalar_one()) == 1


def test_partial_failure_status(client: TestClient, api_session_factory: sessionmaker[Session], monkeypatch) -> None:
    """### A report with documents and errors finalizes as partial_failure with an error row."""
    report = CrawlRunReport(
        results=[
            result(
                source="cba_news_releases",
                documents=(
                    document(
                        source="cba_news_releases", url=f"{SITE}/article/y", native_id="y", type_of_law="News Releases"
                    ),
                ),
                errors=("stopped after hitting max_pages=2",),
            )
        ]
    )
    monkeypatch.setattr(RUNNER_PATH, fixed_runner(report))
    run_id = client.post("/api/v1/crawl-runs", json={"source": "cba"}, headers=HEADERS).json()["id"]
    final = wait_for_run(client, run_id)
    assert final["status"] == "partial_failure"
    errors = client.get(f"/api/v1/crawl-runs/{run_id}/errors").json()
    assert errors["pagination"]["total"] >= 1
    assert errors["items"][0]["error_type"] == "budget_error"


def test_total_failure_status(client: TestClient, monkeypatch) -> None:
    """### A report with no documents and no errors finalizes as failed."""
    report = CrawlRunReport(results=[ActorRunResult(source="cba_news_releases")])
    monkeypatch.setattr(RUNNER_PATH, fixed_runner(report))
    run_id = client.post("/api/v1/crawl-runs", json={"source": "cba"}, headers=HEADERS).json()["id"]
    assert wait_for_run(client, run_id)["status"] == "failed"


# $ Worker failures and lock release$


def test_worker_exception_finalizes_run_and_releases_lock(
    client: TestClient, api_session_factory: sessionmaker[Session], monkeypatch
) -> None:
    """### A worker exception marks the run failed, records an error, and frees the lock."""

    def boom(options: object, settings: object) -> CrawlRunReport:
        raise RuntimeError("worker exploded")

    monkeypatch.setattr(RUNNER_PATH, boom)
    run_id = client.post("/api/v1/crawl-runs", json={"source": "cba"}, headers=HEADERS).json()["id"]
    final = wait_for_run(client, run_id)
    assert final["status"] == "failed"
    assert final["sources"][0]["status"] == "failed"
    errors = client.get(f"/api/v1/crawl-runs/{run_id}/errors").json()
    assert errors["items"][0]["error_type"] == "worker_error"
    with api_session_factory() as session:
        assert int(session.execute(select(func.count()).select_from(CrawlRunSource)).scalar_one()) == 1
        assert int(session.execute(select(func.count()).select_from(CrawlError)).scalar_one()) >= 1
    # $ The advisory lock must be released, so the same source can be triggered again.$
    again = client.post("/api/v1/crawl-runs", json={"source": "cba"}, headers=HEADERS)
    assert again.status_code == 202
    wait_for_run(client, again.json()["id"])


# $ Concurrency$


def test_conflicting_active_run_is_409(client: TestClient, monkeypatch) -> None:
    """### A duplicate active source (or `all`) is rejected with 409; other sources proceed."""
    started = threading.Event()
    release = threading.Event()

    def slow(options: object, settings: object) -> CrawlRunReport:
        started.set()
        release.wait(timeout=10)
        return CrawlRunReport(results=[ActorRunResult(source="cba_news_releases")])

    monkeypatch.setattr(RUNNER_PATH, slow)
    first = client.post("/api/v1/crawl-runs", json={"source": "cba"}, headers=HEADERS)
    assert first.status_code == 202
    first_id = first.json()["id"]
    assert started.wait(timeout=5)

    assert client.post("/api/v1/crawl-runs", json={"source": "cba"}, headers=HEADERS).status_code == 409
    assert client.post("/api/v1/crawl-runs", json={"source": "all"}, headers=HEADERS).status_code == 409
    other = client.post("/api/v1/crawl-runs", json={"source": "commerce"}, headers=HEADERS)
    assert other.status_code == 202

    release.set()
    wait_for_run(client, first_id)
    wait_for_run(client, other.json()["id"])


def test_all_scope_is_accepted_when_free(client: TestClient, monkeypatch) -> None:
    """### `all` acquires every lock and is accepted when nothing is active."""
    monkeypatch.setattr(
        RUNNER_PATH,
        fixed_runner(CrawlRunReport(results=[ActorRunResult(source="cba_news_releases")])),
    )
    response = client.post("/api/v1/crawl-runs", json={"source": "all"}, headers=HEADERS)
    assert response.status_code == 202
    wait_for_run(client, response.json()["id"])
