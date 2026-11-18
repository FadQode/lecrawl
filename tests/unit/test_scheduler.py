"""### Unit tests for the daily scheduler."""

from __future__ import annotations

from lexcrawl import scheduler as scheduler_module
from lexcrawl.config import Settings
from lexcrawl.locking import RunInProgressError
from lexcrawl.scheduler import CrawlScheduler, run_scheduled_job


def _settings(**overrides: object) -> Settings:
    """### Build scheduler settings."""
    values: dict[str, object] = {
        "_env_file": None,
        "schedule_enabled": True,
        "schedule_daily_cron": "0 6 * * *",
        "schedule_timezone": "UTC",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_scheduler_registers_and_stops() -> None:
    """### The scheduler registers one daily job and stops cleanly."""
    scheduler = CrawlScheduler(_settings())
    scheduler.start()
    try:
        assert scheduler.running is True
        assert len(scheduler.jobs()) == 1
        assert scheduler.next_run_time() is not None
    finally:
        scheduler.shutdown()
    assert scheduler.running is False


def test_scheduler_disabled() -> None:
    """### A disabled scheduler registers nothing."""
    scheduler = CrawlScheduler(_settings(schedule_enabled=False))
    scheduler.start()
    assert scheduler.jobs() == []


def test_run_scheduled_job_runs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """### The scheduled job runs run_daily when the advisory lock is free."""
    monkeypatch.setattr(scheduler_module, "run_daily", lambda *args, **kwargs: [])
    assert run_scheduled_job(_settings()) == []


def test_run_scheduled_job_skips_when_locked(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """### An active run is skipped instead of raising into the scheduler thread."""
    def _locked(*args: object, **kwargs: object) -> None:
        """### Simulate a concurrent run holding the advisory lock."""
        raise RunInProgressError("another crawl run is active")

    monkeypatch.setattr(scheduler_module, "run_daily", _locked)
    assert run_scheduled_job(_settings()) == []
