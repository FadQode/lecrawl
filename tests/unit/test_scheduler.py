"""### Unit tests for the daily crawl scheduler."""

from __future__ import annotations

from uuid import UUID, uuid4

from apscheduler.triggers.cron import CronTrigger

from lexcrawl.api.exceptions import ConflictError
from lexcrawl.api.services.crawl_execution import CrawlOptions
from lexcrawl.config import Settings
from lexcrawl.scheduler import DAILY_JOB_ID, CrawlScheduler


class FakeExecutor:
    """### Record scheduler submissions instead of starting a crawl."""

    def __init__(self, *, conflict: bool = False) -> None:
        """### Optionally simulate an active-run conflict."""
        self.calls: list[CrawlOptions] = []
        self._conflict = conflict

    def start(self, options: CrawlOptions) -> UUID:
        """### Record the submitted options and return a synthetic run id."""
        if self._conflict:
            raise ConflictError("a crawl is already active")
        self.calls.append(options)
        return uuid4()


def scheduler_settings(**overrides: object) -> Settings:
    """### Build settings with the scheduler enabled and a fixed cron."""
    payload: dict[str, object] = {
        "schedule_enabled": True,
        "schedule_daily_cron": "0 3 * * *",
        "schedule_timezone": "UTC",
    }
    payload.update(overrides)
    return Settings(_env_file=None, **payload)


def test_scheduler_registers_one_daily_job_and_stops() -> None:
    """### Starting the scheduler registers exactly one cron job; shutdown stops it."""
    scheduler = CrawlScheduler(executor=FakeExecutor(), settings=scheduler_settings())
    scheduler.start()
    try:
        jobs = scheduler.jobs()
        assert scheduler.running is True
        assert len(jobs) == 1
        job = jobs[0]
        assert job.id == DAILY_JOB_ID
        assert isinstance(job.trigger, CronTrigger)
        assert job.max_instances == 1
        assert job.coalesce is True
    finally:
        scheduler.shutdown()
    assert scheduler.running is False


def test_disabled_scheduler_registers_nothing() -> None:
    """### A disabled scheduler does not start or register a job."""
    scheduler = CrawlScheduler(executor=FakeExecutor(), settings=scheduler_settings(schedule_enabled=False))
    scheduler.start()
    assert scheduler.running is False
    assert scheduler.jobs() == []
    scheduler.shutdown()


def test_daily_job_submits_all_sources_as_scheduled() -> None:
    """### The daily job crawls every source with trigger_type 'scheduled'."""
    executor = FakeExecutor()
    scheduler = CrawlScheduler(executor=executor, settings=scheduler_settings())
    scheduler.run_daily_crawl()
    assert len(executor.calls) == 1
    options = executor.calls[0]
    assert options.source == "all"
    assert options.trigger_type == "scheduled"


def test_conflict_is_skipped_without_raising() -> None:
    """### An active crawl makes the scheduled run skip instead of failing the job."""
    scheduler = CrawlScheduler(executor=FakeExecutor(conflict=True), settings=scheduler_settings())
    scheduler.run_daily_crawl()
