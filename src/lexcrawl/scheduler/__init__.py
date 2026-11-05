"""### Daily crawl scheduling via APScheduler.

One daily job runs every source through the existing `CrawlExecutor`, so scheduled crawls share
the same run lifecycle, persistence, and advisory-lock concurrency as API-triggered ones. The
scheduler is started and stopped with the FastAPI application lifespan.
"""

from __future__ import annotations

from apscheduler.job import Job
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from lexcrawl.api.exceptions import ConflictError
from lexcrawl.api.services.crawl_execution import CrawlExecutor, CrawlOptions
from lexcrawl.config import Settings
from lexcrawl.logging import get_logger

LOGGER = get_logger(__name__)

DAILY_JOB_ID = "daily-crawl"


class CrawlScheduler:
    """### Register and run one daily crawl job through the shared executor."""

    def __init__(self, *, executor: CrawlExecutor, settings: Settings) -> None:
        """### Configure the scheduler with the shared executor and schedule settings."""
        self._executor = executor
        self._settings = settings
        self._scheduler = BackgroundScheduler(timezone=settings.schedule_timezone)

    @property
    def running(self) -> bool:
        """### Report whether the background scheduler is running."""
        return bool(self._scheduler.running)

    def jobs(self) -> list[Job]:
        """### Return the registered jobs (empty when disabled)."""
        return list(self._scheduler.get_jobs())

    def start(self) -> None:
        """### Register the daily job and start the scheduler when enabled."""
        if not self._settings.schedule_enabled:
            LOGGER.info("scheduler_disabled")
            return
        trigger = CronTrigger.from_crontab(
            self._settings.schedule_daily_cron,
            timezone=self._settings.schedule_timezone,
        )
        self._scheduler.add_job(
            self.run_daily_crawl,
            trigger=trigger,
            id=DAILY_JOB_ID,
            name="Daily crawl (all sources)",
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
            self._scheduler.shutdown(wait=False)
            LOGGER.info("scheduler_stopped")

    def run_daily_crawl(self) -> None:
        """### Submit one scheduled crawl of every source through the executor."""
        LOGGER.info("scheduled_crawl_triggered")
        try:
            run_id = self._executor.start(CrawlOptions(source="all", trigger_type="scheduled"))
        except ConflictError:
            # $ The advisory lock already prevents overlap; a conflict is expected, not an error.$
            LOGGER.warning("scheduled_crawl_skipped_conflict")
        except Exception:
            LOGGER.exception("scheduled_crawl_failed")
        else:
            LOGGER.info("scheduled_crawl_accepted", extra={"run_id": str(run_id)})
