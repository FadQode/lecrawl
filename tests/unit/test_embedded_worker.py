"""### Unit tests for the in-process queue runner and tolerant API startup."""

from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from lexcrawl.api import app as app_module
from lexcrawl.api.app import create_app
from lexcrawl.api.background import QueueDrainer
from lexcrawl.config import Settings


class _ScriptedJobs:
    """### Pretend queue: returns True for each queued job, then False when empty."""

    def __init__(self, queued: int) -> None:
        """### Start with a number of queued jobs."""
        self.queued = queued
        self.calls = 0
        self._lock = threading.Lock()

    def add(self, count: int = 1) -> None:
        """### Queue more jobs."""
        with self._lock:
            self.queued += count

    def __call__(self) -> bool:
        """### Run one job if any is queued."""
        with self._lock:
            self.calls += 1
            if self.queued == 0:
                return False
            self.queued -= 1
            return True


def test_drains_every_queued_job_then_stops() -> None:
    """### One kick runs jobs until the queue is empty, then the thread exits."""
    jobs = _ScriptedJobs(queued=3)
    drainer = QueueDrainer(jobs)
    assert drainer.kick() is True
    drainer.join(timeout=5)
    assert jobs.queued == 0
    assert jobs.calls == 4  # $ three jobs plus the pass that found the queue empty$
    assert drainer.running is False


def test_kick_during_a_drain_is_picked_up_by_the_same_thread() -> None:
    """### A job queued mid-drain is processed without starting a second thread."""
    release = threading.Event()
    started = threading.Event()
    jobs = _ScriptedJobs(queued=1)

    def run_job() -> bool:
        started.set()
        release.wait(5)
        return jobs()

    drainer = QueueDrainer(run_job)
    assert drainer.kick() is True
    assert started.wait(5)
    jobs.add()
    assert drainer.kick() is False  # $ already running: no second thread$
    release.set()
    drainer.join(timeout=5)
    assert jobs.queued == 0
    assert drainer.running is False


def test_a_failing_pass_stops_the_drain_and_a_later_kick_restarts_it() -> None:
    """### An exception ends the drain without killing future kicks."""
    outcomes: list[object] = [RuntimeError("db down"), True, False]

    def run_job() -> bool:
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return bool(outcome)

    drainer = QueueDrainer(run_job)
    drainer.kick()
    drainer.join(timeout=5)
    assert outcomes == [True, False]
    assert drainer.kick() is True
    drainer.join(timeout=5)
    assert outcomes == []


def test_stop_prevents_new_drains() -> None:
    """### After stop(), kicks are ignored."""
    jobs = _ScriptedJobs(queued=2)
    drainer = QueueDrainer(jobs)
    drainer.stop()
    assert drainer.kick() is False
    assert jobs.calls == 0


def _unreachable_settings(**overrides: object) -> Settings:
    """### Settings pointing at a database that refuses connections."""
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://u:p@127.0.0.1:1/none",
        "db_connect_timeout": 1,
        "api_token": "tok",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_api_starts_even_when_the_database_is_unreachable() -> None:
    """### A failed startup seed is logged; /healthz works and /readyz reports 503."""
    with TestClient(create_app(_unreachable_settings())) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code == 503


def test_embedded_worker_is_built_and_kicked_at_startup(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """### With EMBEDDED_WORKER on, startup builds a drainer and kicks it once."""
    kicks: list[str] = []

    class _FakeDrainer:
        def kick(self) -> bool:
            kicks.append("kick")
            return True

        def stop(self) -> None:
            kicks.append("stop")

    monkeypatch.setattr(app_module, "build_drainer", lambda settings, engine: _FakeDrainer())
    with TestClient(create_app(_unreachable_settings(embedded_worker=True))) as client:
        assert client.app.state.drainer is not None  # type: ignore[attr-defined]
    assert kicks == ["kick", "stop"]


def test_embedded_worker_is_off_by_default(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """### Without the setting, no drainer is created."""
    monkeypatch.setattr(app_module, "build_drainer", lambda settings, engine: (_ for _ in ()).throw(AssertionError))
    with TestClient(create_app(_unreachable_settings())) as client:
        assert client.app.state.drainer is None  # type: ignore[attr-defined]
