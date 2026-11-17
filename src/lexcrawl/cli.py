"""### Command line entry point for LexCrawl crawling, checking, and daily runs."""

from __future__ import annotations

import argparse
import json
import signal
import threading
from datetime import UTC, datetime
from pathlib import Path

from lexcrawl.checks.base import Check
from lexcrawl.checks.change_detection import ChangeDetectionService
from lexcrawl.checks.runner import DailySourceResult, run_daily
from lexcrawl.config import Settings, get_settings
from lexcrawl.crawling.base import Fetcher
from lexcrawl.crawling.http import HttpFetcher
from lexcrawl.factory import (
    CHECK_SOURCES,
    SOURCE_NAMES,
    build_actor,
    build_check,
    build_checkpoint_store,
    build_http_fetcher,
    build_scraper,
    redirect_host_allowlist,
)
from lexcrawl.locking import FileLock, RunInProgressError
from lexcrawl.logging import configure_logging, get_logger
from lexcrawl.orchestrator import CrawlRunReport, run_crawl
from lexcrawl.scheduler import CrawlScheduler

LOG = get_logger(__name__)

CHECK_EXIT_CODES = {
    "no_change": 0,
    "new_data": 1,
    "check_failed": 2,
    "anomaly": 2,
    "budget_exceeded": 2,
    "failed": 2,
}


def _add_logging_options(parser: argparse.ArgumentParser) -> None:
    """### Attach the shared logging options to a subcommand parser."""
    parser.add_argument("--log-level", default=None)
    parser.add_argument("--log-format", choices=["json", "console"], default=None)


def build_parser() -> argparse.ArgumentParser:
    """### Build the argument parser for the `lexcrawl` command."""
    parser = argparse.ArgumentParser(prog="lexcrawl", description="LexCrawl crawling and ingestion CLI")
    subcommands = parser.add_subparsers(dest="command", required=True)

    crawl = subcommands.add_parser("crawl", help="run one or more source actors")
    crawl.add_argument("--source", choices=list(SOURCE_NAMES), default="cba")
    crawl.add_argument("--max-pages", type=int, default=None, help="maximum listing pages per source")
    crawl.add_argument("--request-delay", type=float, default=None, help="seconds between requests")
    crawl.add_argument("--timeout", type=float, default=None, help="per-request timeout in seconds")
    crawl.add_argument("--max-retries", type=int, default=None, help="retries for transient failures")
    crawl.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSON output: a file path, or a directory to receive a timestamped file (defaults to data/)",
    )
    _add_logging_options(crawl)

    check = subcommands.add_parser("check", help="detect whether a source has changed")
    check.add_argument("--source", choices=[*CHECK_SOURCES, "all"], default="all")
    check.add_argument("--dry-run", action="store_true", help="report without updating the stored watermark")
    _add_logging_options(check)

    daily = subcommands.add_parser("daily", help="probe every source, then crawl only the ones that changed")
    daily.add_argument("--source", choices=[*CHECK_SOURCES, "all"], default="all")
    daily.add_argument("--force", action="store_true", help="bypass change detection and crawl anyway")
    daily.add_argument("--bootstrap", action="store_true", help="ingest everything once, ignoring checkpoints")
    daily.add_argument("--dry-run", action="store_true", help="report without writing watermark or identities")
    _add_logging_options(daily)

    schedule = subcommands.add_parser("schedule", help="run the daily job on a cron until stopped")
    _add_logging_options(schedule)

    serve = subcommands.add_parser("serve", help="run the HTTP API (FastAPI/uvicorn)")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    _add_logging_options(serve)

    worker = subcommands.add_parser("worker", help="claim and run queued crawl jobs")
    worker.add_argument("--once", action="store_true", help="process at most one queued job then exit")
    _add_logging_options(worker)
    return parser


def build_fetcher(arguments: argparse.Namespace, settings: Settings) -> Fetcher:
    """### Build the HTTP crawling strategy from CLI overrides and settings."""
    return HttpFetcher(
        user_agent=settings.crawl_user_agent,
        timeout_seconds=arguments.timeout if arguments.timeout is not None else settings.crawl_request_timeout_seconds,
        max_retries=arguments.max_retries if arguments.max_retries is not None else settings.crawl_max_retries,
        backoff_seconds=settings.crawl_retry_backoff_seconds,
        request_delay_seconds=(
            arguments.request_delay if arguments.request_delay is not None else settings.crawl_request_delay_seconds
        ),
        max_response_bytes=settings.crawl_max_response_bytes,
        allowed_redirect_hosts=redirect_host_allowlist(settings),
    )


def render_summary(report: CrawlRunReport) -> str:
    """### Render a human-readable run summary."""
    lines = [f"status={report.status} documents={len(report.documents)} duration={report.duration_seconds:.2f}s"]
    for result in report.results:
        lines.append(
            f"  {result.source}: status={result.status} documents={len(result.documents)} "
            f"listing={result.listing_pages_succeeded}/{result.listing_pages_attempted} "
            f"details={result.detail_pages_succeeded}/{result.detail_pages_attempted} "
            f"discovered={result.articles_discovered} dedup={result.article_urls_deduplicated} "
            f"filtered={result.records_filtered_out} skipped_known={result.records_skipped_known} "
            f"metadata_only={result.records_without_text} pagination_complete={result.pagination_complete}"
        )
        lines.extend(f"    error: {error}" for error in result.errors)
    return "\n".join(lines)


def render_daily(result: DailySourceResult) -> str:
    """### Render a human-readable line for one source in a daily run."""
    return (
        f"  {result.source}: {result.outcome} ({result.duration_seconds:.2f}s) - "
        f"documents={result.documents} skipped_known={result.skipped_known} "
        f"pending={result.pending} credits={result.credits} - {result.detail}"
    )


def output_path(arguments: argparse.Namespace, settings: Settings) -> Path:
    """### Resolve the JSON output file, naming timestamped files for directories."""
    requested = arguments.out if arguments.out is not None else Path(settings.output_dir)
    if requested.suffix:
        return requested
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    return requested / f"{stamp}-{arguments.source}.json"


def write_documents(path: Path, report: CrawlRunReport) -> None:
    """### Write collected canonical documents to a JSON file, one row per object."""
    payload = [document.model_dump(mode="json") for document in report.documents]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_checks(source: str, settings: Settings) -> list[Check]:
    """### Build the change probe for each requested source."""
    names = CHECK_SOURCES if source == "all" else (source,)
    checks: list[Check] = []
    try:
        for name in names:
            scraper = build_scraper(name, settings)
            fetcher = build_http_fetcher(settings)
            checks.append(build_check(name, settings, scraper=scraper, http_fetcher=fetcher))
    except ValueError:
        for check in checks:
            check.close()
        raise
    return checks


def run_checks(arguments: argparse.Namespace, settings: Settings) -> int:
    """### Run the requested checks and return a gate exit code (0 same, 1 new, 2 failed)."""
    try:
        checks = build_checks(arguments.source, settings)
    except ValueError as error:
        print(f"configuration error: {error}")
        return 2

    store = build_checkpoint_store(settings)
    exit_code = 0
    try:
        for check in checks:
            probe = check.probe()
            decision = ChangeDetectionService(store, record=not arguments.dry_run).decide(check.name, probe)
            print(f"{check.name}: {decision.outcome} ({probe.duration_seconds:.2f}s) - {probe.summary}")
            if probe.error is not None:
                print(f"  error: {probe.error}")
            exit_code = max(exit_code, CHECK_EXIT_CODES[decision.outcome])
            LOG.info(
                "check_finished",
                extra={
                    "source": check.name,
                    "outcome": decision.outcome,
                    "duration_seconds": probe.duration_seconds,
                },
            )
    finally:
        for check in checks:
            check.close()
        store.close()
    return exit_code


def run_daily_command(arguments: argparse.Namespace, settings: Settings) -> int:
    """### Run the daily probe-then-crawl flow and return a gate exit code."""
    names = tuple(CHECK_SOURCES) if arguments.source == "all" else (arguments.source,)
    lock = FileLock(Path(settings.run_lock_path))
    if not lock.acquire():
        print("a daily run is already in progress; skipping")
        return 2
    try:
        results = run_daily(
            settings,
            sources=names,
            force=arguments.force,
            bootstrap=arguments.bootstrap,
            record=not arguments.dry_run,
            trigger="manual",
        )
    except RunInProgressError:
        print("a crawl run is already active; try again later")
        return 2
    except ValueError as error:
        print(f"configuration error: {error}")
        return 2
    finally:
        lock.release()

    print(f"daily status: {len(results)} source(s)")
    exit_code = 0
    for result in results:
        print(render_daily(result))
        exit_code = max(exit_code, CHECK_EXIT_CODES[result.outcome])
    return exit_code


def run_serve_command(arguments: argparse.Namespace, settings: Settings) -> int:
    """### Run the FastAPI application with uvicorn."""
    import uvicorn

    from lexcrawl.api.app import create_app

    app = create_app(settings)
    uvicorn.run(app, host=arguments.host or settings.api_host, port=arguments.port or settings.api_port)
    return 0


def run_worker_command(arguments: argparse.Namespace, settings: Settings) -> int:
    """### Claim and run queued crawl jobs (once, or until interrupted)."""
    from lexcrawl.worker import run_once

    try:
        if arguments.once:
            processed = run_once(settings)
            print("processed a job" if processed else "no queued jobs")
            return 0
        stop = threading.Event()

        def _handle(signum: int, frame: object) -> None:
            """### Signal the worker loop to stop."""
            stop.set()

        previous_int = signal.signal(signal.SIGINT, _handle)
        previous_term = signal.signal(signal.SIGTERM, _handle)
        try:
            while not stop.is_set():
                if not run_once(settings):
                    stop.wait(5.0)
        finally:
            signal.signal(signal.SIGINT, previous_int)
            signal.signal(signal.SIGTERM, previous_term)
        return 0
    except ValueError as error:
        print(f"configuration error: {error}")
        return 2


def run_schedule_command(settings: Settings) -> int:
    """### Run the daily job on a cron until interrupted."""
    lock = FileLock(Path(settings.schedule_lock_path))
    if not lock.acquire():
        print("another scheduler is already running")
        return 2

    scheduler = CrawlScheduler(settings)
    stop = threading.Event()

    def _handle(signum: int, frame: object) -> None:
        """### Signal the main loop to stop on a termination signal."""
        stop.set()

    previous_int = signal.signal(signal.SIGINT, _handle)
    previous_term = signal.signal(signal.SIGTERM, _handle)
    try:
        scheduler.start()
        if not settings.schedule_enabled:
            print("scheduler disabled (SCHEDULE_ENABLED=false)")
            return 0
        print(
            f"scheduler started: cron={settings.schedule_daily_cron} "
            f"tz={settings.schedule_timezone} next={scheduler.next_run_time()}"
        )
        while not stop.is_set():
            stop.wait(1.0)
        print("shutting down")
    finally:
        scheduler.shutdown()
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        lock.release()
    return 0


def run_crawl_command(arguments: argparse.Namespace, settings: Settings) -> int:
    """### Build the actor and run one crawl, returning a process exit code."""
    fetcher = build_fetcher(arguments, settings)
    scraper = None
    try:
        scraper = build_scraper(arguments.source, settings)
        actor = build_actor(
            arguments.source,
            settings,
            scraper=scraper,
            http_fetcher=fetcher,
            max_pages=arguments.max_pages,
        )
    except ValueError as error:
        # $ Missing source locations are operator errors, so report them without a traceback.$
        fetcher.close()
        print(f"configuration error: {error}")
        return 2

    try:
        report = run_crawl([actor], fetcher)
    finally:
        fetcher.close()
        closer = getattr(scraper, "close", None)
        if callable(closer):
            closer()

    print(render_summary(report))
    if report.documents:
        destination = output_path(arguments, settings)
        write_documents(destination, report)
        print(f"records written to {destination}")
        LOG.info("crawl_finished", extra=report.summary() | {"output": str(destination)})
    else:
        # $ Never leave an empty JSON file that a reader could mistake for collected data.$
        print("no records collected; nothing written")
        LOG.info("crawl_finished", extra=report.summary() | {"output": None})

    return 0 if report.status == "success" else 1


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    """### Run the CLI and return a process exit code."""
    arguments = build_parser().parse_args(argv)
    settings = settings or get_settings()
    configure_logging(
        getattr(arguments, "log_level", None) or settings.log_level,
        getattr(arguments, "log_format", None) or settings.log_format,
    )

    if arguments.command == "check":
        return run_checks(arguments, settings)
    if arguments.command == "daily":
        return run_daily_command(arguments, settings)
    if arguments.command == "schedule":
        return run_schedule_command(settings)
    if arguments.command == "worker":
        return run_worker_command(arguments, settings)
    if arguments.command == "serve":
        return run_serve_command(arguments, settings)
    return run_crawl_command(arguments, settings)


if __name__ == "__main__":
    raise SystemExit(main())
