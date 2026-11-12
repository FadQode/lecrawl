"""### Daily scheduler: APScheduler fires the incremental daily run on a cron."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from apscheduler.job import Job
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from lexcrawl.checks.runner import DailySourceResult, run_daily
from lexcrawl.config import Settings
from lexcrawl.factory import CHECK_SOURCES
from lexcrawl.locking import FileLock
from lexcrawl.logging import get_logger

LOGGER = get_logger(__name__)

DAILY_JOB_ID = "daily-run"


def run_scheduled_job(settings: Settings) -> list[DailySourceResult]:
    """### Run the daily flow under the run lock, skipping if another run holds it."""
    lock = FileLock(Path(settings.run_lock_path))
    if not lock.acquire():
        LOGGER.warning("scheduled_run_skipped_locked")
        return []
    try:
        results = run_daily(settings, sources=CHECK_SOURCES)
    except Exception:
        # $ A scheduled failure is logged, never raised into the scheduler thread.$
        LOGGER.exception("scheduled_daily_failed")
        return []
    finally:
        lock.release()
    for result in results:
        LOGGER.info(
            "scheduled_daily_source",
            extra={
                "source": result.source,
                "outcome": result.outcome,
                "documents": result.documents,
                "credits": result.credits,
            },
        )
    return results


class CrawlScheduler:
    """### Register and run the daily crawl job through the shared runner."""

    def __init__(self, settings: Settings) -> None:
        """### Configure the background scheduler with the schedule settings."""
        self._settings = settings
        self._scheduler = BackgroundScheduler(timezone=settings.schedule_timezone)

    @property
    def running(self) -> bool:
        """### Report whether the background scheduler is running."""
        return bool(self._scheduler.running)

    def jobs(self) -> list[Job]:
        """### Return the registered jobs."""
        return list(self._scheduler.get_jobs())

    def next_run_time(self) -> datetime | None:
        """### Return the next fire time, or None when no job is registered."""
        jobs = self.jobs()
        return jobs[0].next_run_time if jobs else None

    def start(self) -> None:
        """### Register the daily job and start the scheduler when enabled."""
        if not self._settings.schedule_enabled:
            LOGGER.info("scheduler_disabled")
            return
        trigger = CronTrigger.from_crontab(
            self._settings.schedule_daily_cron, timezone=self._settings.schedule_timezone
        )
        self._scheduler.add_job(
            run_scheduled_job,
            args=[self._settings],
            trigger=trigger,
            id=DAILY_JOB_ID,
            name="Daily incremental run",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
        self._scheduler.start()
        LOGGER.info(
            "scheduler_started",
            extra={"cron": self._settings.schedule_daily_cron, "timezone": self._settings.schedule_timezone},
        )

    def shutdown(self) -> None:
        """### Stop the scheduler if it is running."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=True)
            LOGGER.info("scheduler_stopped")
