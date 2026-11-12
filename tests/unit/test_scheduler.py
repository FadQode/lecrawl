"""### Unit tests for the monthly usage store and the daily scheduler wiring."""

from __future__ import annotations

import re
from pathlib import Path

from lexcrawl.checks.usage import UsageStore, current_period
from lexcrawl.config import Settings
from lexcrawl.scheduler import DAILY_JOB_ID, CrawlScheduler


def test_period_is_year_month() -> None:
    """### The period key is a YYYY-MM month."""
    assert re.fullmatch(r"\d{4}-\d{2}", current_period())


def test_usage_add_and_read(tmp_path: Path) -> None:
    """### Credits accumulate per period and persist."""
    store = UsageStore(tmp_path / "usage.json")
    assert store.spent("2026-11") == 0
    assert store.add("2026-11", 5) == 5
    assert store.add("2026-11", 2) == 7
    assert UsageStore(tmp_path / "usage.json").spent("2026-11") == 7


def test_usage_ignores_non_positive(tmp_path: Path) -> None:
    """### Adding zero or negative credits is a no-op."""
    store = UsageStore(tmp_path / "usage.json")
    assert store.add("2026-11", 0) == 0
    assert store.add("2026-11", -3) == 0


def test_usage_corrupt_is_zero(tmp_path: Path) -> None:
    """### A corrupt usage file reads as zero."""
    path = tmp_path / "usage.json"
    path.write_text("{bad", encoding="utf-8")
    assert UsageStore(path).spent("2026-11") == 0


def test_scheduler_registers_daily_job(tmp_path: Path) -> None:
    """### The scheduler registers one daily job with a next run time."""
    settings = Settings(
        _env_file=None,
        schedule_enabled=True,
        schedule_daily_cron="0 6 * * *",
        schedule_timezone="UTC",
        run_lock_path=str(tmp_path / "run.lock"),
    )
    scheduler = CrawlScheduler(settings)
    scheduler.start()
    try:
        jobs = scheduler.jobs()
        assert len(jobs) == 1
        assert jobs[0].id == DAILY_JOB_ID
        assert scheduler.next_run_time() is not None
        assert scheduler.running is True
    finally:
        scheduler.shutdown()


def test_scheduler_disabled_registers_nothing(tmp_path: Path) -> None:
    """### A disabled scheduler registers no job and does not start."""
    settings = Settings(_env_file=None, schedule_enabled=False, run_lock_path=str(tmp_path / "run.lock"))
    scheduler = CrawlScheduler(settings)
    scheduler.start()
    assert scheduler.jobs() == []
    assert scheduler.running is False
