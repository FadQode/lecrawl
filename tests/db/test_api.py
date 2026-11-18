"""### API tests (FastAPI TestClient against the live database)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from lexcrawl.api.app import create_app
from lexcrawl.config import get_settings
from lexcrawl.db.models import CrawlRun, Document, Job, Source
from lexcrawl.db.session import session_scope
from lexcrawl.models.document import CanonicalDocument, compute_content_hash
from lexcrawl.repositories.crawl_runs import finish_run, record_source, start_run
from lexcrawl.repositories.documents import upsert_document
from lexcrawl.repositories.sources import ensure_source, source_id_for

TEST_SOURCE = "test_api_source"


def _settings(token: str):  # type: ignore[no-untyped-def]
    return get_settings().model_copy(update={"api_token": token})


def _document(title: str, published: date) -> CanonicalDocument:
    body = f"TEXT-BEGIN\n{title} body"
    return CanonicalDocument(
        source=TEST_SOURCE, source_url=f"https://t.test/{title.lower()}", title=title, language="en",
        text=body, content_hash=compute_content_hash(body), extraction_status="complete",
        published_at=published, published_precision="day",
    )


def _cleanup(factory) -> None:  # type: ignore[no-untyped-def]
    with session_scope(factory) as session:
        source_id = source_id_for(session, TEST_SOURCE)
        if source_id is not None:
            session.execute(delete(Document).where(Document.source_id == source_id))
        session.execute(delete(Source).where(Source.code == TEST_SOURCE))
    with session_scope(factory) as session:
        session.execute(delete(Job))


@pytest.mark.db
def test_auth_required(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### Reads need a bearer token; an unset token disables the API."""
    with TestClient(create_app(_settings(""))) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/sources").status_code == 503
    with TestClient(create_app(_settings("tok"))) as client:
        assert client.get("/sources").status_code == 401
        assert client.get("/sources", headers={"Authorization": "Bearer tok"}).status_code == 200


@pytest.mark.db
def test_document_reads_and_pagination(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### Lists omit text and enforce bounded, cursor-validated pagination."""
    now = datetime.now(tz=UTC)
    _cleanup(pg_factory)
    try:
        with session_scope(pg_factory) as session:
            source_id = ensure_source(session, TEST_SOURCE, "API source")
            upsert_document(session, source_id=source_id, document=_document("A", date(2026, 1, 1)), now=now)
            upsert_document(session, source_id=source_id, document=_document("B", date(2026, 2, 1)), now=now)
        with TestClient(create_app(_settings("tok")), headers={"Authorization": "Bearer tok"}) as client:
            page = client.get("/documents", params={"source": TEST_SOURCE, "limit": 1}).json()
            assert len(page["items"]) == 1 and "text" not in page["items"][0] and page["has_more"] is True
            assert client.get("/documents", params={"source": TEST_SOURCE, "limit": 201}).status_code == 422
            mismatch = client.get(
                "/documents", params={"source": TEST_SOURCE, "topic": "x", "cursor": page["next_cursor"]}
            )
            assert mismatch.status_code == 400
            doc_id = page["items"][0]["id"]
            assert client.get(f"/documents/{doc_id}").json()["text"].startswith("TEXT-BEGIN")
            assert client.get("/documents/999999999").status_code == 404
    finally:
        _cleanup(pg_factory)


@pytest.mark.db
def test_job_submission(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### Jobs fan out one job per source, dedupe by idempotency, and conflict per source."""
    _cleanup(pg_factory)
    try:
        with TestClient(create_app(_settings("tok"))) as client:
            headers = {"Authorization": "Bearer tok"}
            assert client.post("/jobs", json={}).status_code == 401
            first = client.post(
                "/jobs", headers={**headers, "Idempotency-Key": "api-test-1"}, json={"sources": ["uae"]}
            )
            assert first.status_code == 202
            assert [job["source"] for job in first.json()["jobs"]] == ["uae"]
            again = client.post(
                "/jobs", headers={**headers, "Idempotency-Key": "api-test-1"}, json={"sources": ["uae"]}
            )
            assert again.json()["jobs"][0]["run_id"] == first.json()["jobs"][0]["run_id"]
            # $ Different sources are independent, so they are accepted while uae is active.$
            fanout = client.post("/jobs", headers=headers, json={"sources": ["commerce", "cba"]})
            assert fanout.status_code == 202
            assert {job["source"] for job in fanout.json()["jobs"]} == {"commerce", "cba"}
        with TestClient(create_app(_settings("tok"))) as client:
            busy = client.post("/jobs", headers={"Authorization": "Bearer tok"}, json={"sources": ["uae"]})
            assert busy.status_code == 409
    finally:
        _cleanup(pg_factory)


@pytest.mark.db
def test_run_and_analytics_endpoints(pg_factory) -> None:  # type: ignore[no-untyped-def]
    """### Run listings/detail and analytics endpoints return data."""
    now = datetime.now(tz=UTC)
    _cleanup(pg_factory)
    run_id: int | None = None
    try:
        with session_scope(pg_factory) as session:
            source_id = ensure_source(session, TEST_SOURCE, "API source")
            run_id, run_uuid = start_run(session, trigger_type="manual", now=now)
            record_source(
                session, crawl_run_id=run_id, source_id=source_id, check_outcome="new_data", status="success",
                discovered=1, inserted=1, updated=0, skipped=0, failed=0, resources=0, pending=0,
                credits=1, pagination_complete=True, detail="ok", started_at=now, finished_at=now,
            )
            finish_run(session, crawl_run_id=run_id, status="success", credits=1, duration_ms=5, now=now)
        with TestClient(create_app(_settings("tok")), headers={"Authorization": "Bearer tok"}) as client:
            assert client.get("/runs").status_code == 200
            detail = client.get(f"/runs/{run_uuid}")
            assert detail.status_code == 200 and detail.json()["sources"]
            assert client.get("/runs/not-a-uuid").status_code == 404
            assert client.get("/analytics/credits").status_code == 200
            assert client.get("/analytics/pending").status_code == 200
    finally:
        with session_scope(pg_factory) as session:
            if run_id is not None:
                session.execute(delete(CrawlRun).where(CrawlRun.id == run_id))
        _cleanup(pg_factory)
