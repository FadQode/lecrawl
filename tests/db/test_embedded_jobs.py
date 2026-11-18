"""### DB test: POST /jobs kicks the embedded worker after its jobs are committed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from lexcrawl.api import app as app_module
from lexcrawl.api.app import create_app
from lexcrawl.config import get_settings
from lexcrawl.db.models import Job
from lexcrawl.db.session import session_scope
from lexcrawl.repositories.jobs import count_by_status


@pytest.mark.db
def test_submit_job_kicks_the_embedded_worker(pg_factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """### The kick happens after commit, so a real runner would find the queued job."""
    with session_scope(pg_factory) as session:
        session.execute(delete(Job))
    queued_at_kick: list[int] = []

    class _FakeDrainer:
        def kick(self) -> bool:
            with session_scope(pg_factory) as session:
                queued_at_kick.append(count_by_status(session).get("queued", 0))
            return True

        def stop(self) -> None:
            return None

    monkeypatch.setattr(app_module, "build_drainer", lambda settings, engine: _FakeDrainer())
    settings = get_settings().model_copy(update={"api_token": "tok", "embedded_worker": True})
    try:
        with TestClient(create_app(settings), headers={"Authorization": "Bearer tok"}) as client:
            queued_at_kick.clear()  # $ ignore the startup kick$
            response = client.post("/jobs", json={"sources": ["cba"]})
            assert response.status_code == 202
        assert queued_at_kick == [1]
    finally:
        with session_scope(pg_factory) as session:
            session.execute(delete(Job))
