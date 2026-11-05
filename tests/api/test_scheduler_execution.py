"""### Integration test: the scheduled job persists through the shared executor lifecycle."""

from __future__ import annotations

import time
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lexcrawl.api.services.crawl_execution import CrawlExecutor
from lexcrawl.config import Settings
from lexcrawl.db.models import CrawlRun, Document
from lexcrawl.orchestrator import CrawlRunReport
from lexcrawl.scheduler import CrawlScheduler
from tests.integration.db.builders import SITE, document, result


def test_scheduled_execution_persists_as_scheduled(
    api_engine,
    api_session_factory: sessionmaker[Session],
    api_database_url: str,
) -> None:
    """### A scheduled run uses trigger_type 'scheduled' and the same persistence lifecycle."""
    settings = Settings(
        _env_file=None, database_url=api_database_url, schedule_enabled=True, db_pool_size=2, db_max_overflow=2
    )

    def runner(options: object, resolved: Settings) -> CrawlRunReport:
        """### Return a successful report without crawling the network."""
        return CrawlRunReport(
            results=[
                result(
                    source="cba_news_releases",
                    documents=(
                        document(
                            source="cba_news_releases",
                            url=f"{SITE}/article/scheduled",
                            native_id="scheduled",
                            type_of_law="News Releases",
                            published_at=date(2025, 1, 1),
                        ),
                    ),
                    duration_seconds=0.1,
                )
            ]
        )

    executor = CrawlExecutor(settings=settings, engine=api_engine, session_factory=api_session_factory, runner=runner)
    scheduler = CrawlScheduler(executor=executor, settings=settings)
    try:
        scheduler.run_daily_crawl()
        deadline = time.time() + 10
        trigger_type: str | None = None
        status: str | None = None
        while time.time() < deadline:
            with api_session_factory() as session:
                run = session.execute(select(CrawlRun).order_by(CrawlRun.id.desc())).scalars().first()
                trigger_type = run.trigger_type if run is not None else None
                status = run.status if run is not None else None
            if status is not None and status != "running":
                break
            time.sleep(0.02)
        assert trigger_type == "scheduled"
        assert status == "success"
        with api_session_factory() as session:
            assert session.execute(select(Document)).scalars().first() is not None
    finally:
        executor.shutdown()
