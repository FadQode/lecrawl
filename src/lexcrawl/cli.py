"""### Command line entry point for running LexCrawl source actors."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from lexcrawl.actors.base import Actor
from lexcrawl.actors.cba import CbaNewsReleasesActor
from lexcrawl.actors.commerce import CommerceReportsActor
from lexcrawl.actors.uae_legislation import UaeLegislationActor
from lexcrawl.config import Settings, get_settings
from lexcrawl.crawling.base import Fetcher
from lexcrawl.crawling.browser import BrowserFetcher
from lexcrawl.crawling.firecrawl import FirecrawlClient
from lexcrawl.crawling.http import HttpFetcher
from lexcrawl.crawling.rotating import RotatingFetcher
from lexcrawl.db.engine import create_db_engine
from lexcrawl.db.seed import seed_sources
from lexcrawl.db.session import create_session_factory, session_scope
from lexcrawl.logging import configure_logging, get_logger
from lexcrawl.orchestrator import CrawlRunReport, run_crawl
from lexcrawl.persistence import persist_crawl_report
from lexcrawl.repositories.documents import IncompleteReport, incomplete_reports, known_identity_keys

if TYPE_CHECKING:
    from alembic.config import Config

LOG = get_logger(__name__)

SOURCE_NAMES = ("uae", "commerce", "cba")
COMMERCE_SOURCE_CODE = "commerce_reports"


def build_parser() -> argparse.ArgumentParser:
    """### Build the argument parser for the `lexcrawl` command."""
    parser = argparse.ArgumentParser(prog="lexcrawl", description="LexCrawl crawling and ingestion CLI")
    subcommands = parser.add_subparsers(dest="command", required=True)
    crawl = subcommands.add_parser("crawl", help="run one or more source actors")
    crawl.add_argument("--source", choices=[*SOURCE_NAMES, "all"], default="all")
    crawl.add_argument("--engine", choices=["http", "browser"], default=None, help="crawling strategy to use")
    crawl.add_argument("--headed", action="store_true", help="show the browser window (browser engine only)")
    crawl.add_argument("--max-pages", type=int, default=None, help="maximum listing pages per source")
    crawl.add_argument("--request-delay", type=float, default=None, help="seconds between requests")
    crawl.add_argument("--timeout", type=float, default=None, help="per-request timeout in seconds")
    crawl.add_argument("--max-retries", type=int, default=None, help="retries for transient failures")
    crawl.add_argument("--concurrency", type=int, default=None, help="bounded concurrent detail fetches")
    crawl.add_argument("--no-robots", action="store_true", help="skip robots.txt checks")
    crawl.add_argument("--bilingual", action="store_true", help="collect secondary-language localizations")
    crawl.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSON output: a file path, or a directory to receive a timestamped file (defaults to data/)",
    )
    crawl.add_argument("--log-level", default=None)
    crawl.add_argument("--log-format", choices=["json", "console"], default=None)
    crawl.add_argument(
        "--bootstrap",
        action="store_true",
        help="explicitly authorise discovery and acquisition of historical Commerce reports",
    )
    crawl.add_argument(
        "--persist",
        action="store_true",
        help="persist this run and its documents to PostgreSQL in addition to JSON output",
    )

    database = subcommands.add_parser("db", help="database schema and registry maintenance")
    database_commands = database.add_subparsers(dest="db_command", required=True)
    upgrade = database_commands.add_parser("upgrade", help="apply pending migrations")
    upgrade.add_argument("--revision", default="head", help="target revision (defaults to head)")
    database_commands.add_parser("current", help="show the applied revision")
    revision = database_commands.add_parser("revision", help="create an empty migration revision")
    revision.add_argument("-m", "--message", required=True, help="revision message")
    database_commands.add_parser("seed", help="insert the source registry entries")
    return parser


def build_firecrawl(settings: Settings) -> FirecrawlClient:
    """### Build the Firecrawl scraper from validated settings."""
    return FirecrawlClient(
        api_key=settings.firecrawl_api_key,
        api_url=settings.firecrawl_api_url,
        timeout_seconds=settings.firecrawl_timeout_seconds,
        max_retries=settings.firecrawl_max_retries,
        request_delay_seconds=settings.firecrawl_request_delay_seconds,
        cache_dir=settings.firecrawl_cache_dir or None,
    )


def known_identities_provider(settings: Settings) -> Callable[[], set[str]]:
    """### Build a lazy lookup of persisted Commerce report identities."""

    def lookup() -> set[str]:
        """### Read the persisted identities for the Commerce source."""
        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        try:
            with session_scope(factory) as session:
                return known_identity_keys(session, source_code=COMMERCE_SOURCE_CODE)
        finally:
            engine.dispose()

    return lookup


def incomplete_reports_provider(settings: Settings) -> Callable[[], list[IncompleteReport]]:
    """### Build a lazy lookup of incomplete Commerce reports eligible for retry."""

    def lookup() -> list[IncompleteReport]:
        """### Read the incomplete reports whose retry budget is not yet spent."""
        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        try:
            with session_scope(factory) as session:
                return incomplete_reports(
                    session,
                    source_code=COMMERCE_SOURCE_CODE,
                    max_attempts=settings.firecrawl_incomplete_max_attempts,
                    limit=settings.firecrawl_incomplete_retries_per_run,
                )
        finally:
            engine.dispose()

    return lookup


def crawl_engine(arguments: argparse.Namespace, settings: Settings) -> str:
    """### Resolve the crawling strategy from the CLI or settings."""
    return arguments.engine or settings.crawl_engine


def engine_for_source(name: str, arguments: argparse.Namespace, settings: Settings) -> str:
    """### Resolve one source's engine: explicit flag, per-source default, then global."""
    if arguments.engine:
        return str(arguments.engine)
    if name == "uae":
        # $ The primary source refuses plain HTTP, so it renders through the browser by default.$
        return settings.uae_crawl_engine
    return settings.crawl_engine


def build_actors(arguments: argparse.Namespace, settings: Settings) -> list[Actor]:
    """### Instantiate the actors selected on the command line."""
    names = list(SOURCE_NAMES) if arguments.source == "all" else [arguments.source]
    return [build_actor(name, arguments, settings) for name in names]


def build_actor(name: str, arguments: argparse.Namespace, settings: Settings) -> Actor:
    """### Build one actor from CLI overrides and validated settings."""
    if name == "uae":
        return UaeLegislationActor(
            listing_url=settings.listings_url,
            language=settings.listings_language,
            max_listing_pages=page_bound(arguments, settings),
            concurrency=concurrency(arguments, settings, engine=engine_for_source(name, arguments, settings)),
            bilingual=settings.crawl_bilingual or arguments.bilingual,
            secondary_listing_url=settings.listings_secondary_url,
        )
    if name == "commerce":
        # $ Firecrawl is bounded by explicit page and detail-scrape budgets.$
        return CommerceReportsActor(
            listing_url=settings.reports_listing_url,
            firecrawl=build_firecrawl(settings),
            known_identities=known_identities_provider(settings),
            incomplete_reports=incomplete_reports_provider(settings),
            bootstrap=getattr(arguments, "bootstrap", False),
            max_pages=arguments.max_pages if arguments.max_pages is not None else settings.firecrawl_max_pages_per_run,
            max_detail_scrapes=settings.firecrawl_max_detail_scrapes_per_run,
            max_incomplete_retries=settings.firecrawl_incomplete_retries_per_run,
            min_body_words=settings.firecrawl_min_body_words,
            max_pdf_bytes=settings.pdf_max_bytes,
            pdf_timeout_seconds=settings.pdf_extraction_timeout_seconds,
        )
    if name == "cba":
        return CbaNewsReleasesActor(
            api_url=settings.releases_api_url,
            site_url=settings.releases_site_url,
            api_language=settings.releases_api_language,
            type_guid=settings.releases_type_id,
            article_path=settings.releases_article_path,
            page_size=settings.releases_page_size,
            max_pages=arguments.max_pages if arguments.max_pages is not None else settings.releases_max_pages,
            max_pdf_bytes=settings.pdf_max_bytes,
            pdf_timeout_seconds=settings.pdf_extraction_timeout_seconds,
        )
    raise ValueError(f"unknown source {name!r}")


def page_bound(arguments: argparse.Namespace, settings: Settings) -> int:
    """### Resolve the listing page bound from the CLI or settings."""
    return arguments.max_pages if arguments.max_pages is not None else settings.crawl_max_listing_pages


def concurrency(arguments: argparse.Namespace, settings: Settings, *, engine: str | None = None) -> int:
    """### Resolve detail concurrency, forcing a single thread for the browser."""
    if (engine or crawl_engine(arguments, settings)) == "browser":
        return 1
    return arguments.concurrency if arguments.concurrency is not None else settings.crawl_concurrency_per_source


def request_timeout(arguments: argparse.Namespace, settings: Settings) -> float:
    """### Resolve the per-request timeout from the CLI or settings."""
    return arguments.timeout if arguments.timeout is not None else settings.crawl_request_timeout_seconds


def retry_budget(arguments: argparse.Namespace, settings: Settings) -> int:
    """### Resolve the transient retry budget from the CLI or settings."""
    return arguments.max_retries if arguments.max_retries is not None else settings.crawl_max_retries


def request_delay(arguments: argparse.Namespace, settings: Settings) -> float:
    """### Resolve the request pacing delay from the CLI or settings."""
    return arguments.request_delay if arguments.request_delay is not None else settings.crawl_request_delay_seconds


def robots_enabled(arguments: argparse.Namespace, settings: Settings) -> bool:
    """### Report whether the robots policy must be enforced."""
    return settings.crawl_respect_robots_txt and not arguments.no_robots


def redirect_host_allowlist(settings: Settings) -> tuple[str, ...]:
    """### Allow redirects among explicitly configured source hosts and their www aliases."""
    configured = (
        settings.listings_url,
        settings.listings_secondary_url,
        settings.reports_api_url,
        settings.reports_article_host,
        settings.releases_api_url,
        settings.releases_site_url,
    )
    hosts: set[str] = set()
    for value in configured:
        host = (urlparse(value).hostname or value).lower().rstrip(".")
        if not host:
            continue
        hosts.add(host)
        hosts.add(host[4:] if host.startswith("www.") else f"www.{host}")
    return tuple(sorted(hosts))


def build_fetcher(arguments: argparse.Namespace, settings: Settings, *, engine: str | None = None) -> Fetcher:
    """### Build the crawling strategy selected by the CLI, settings, or source default."""
    if (engine or crawl_engine(arguments, settings)) == "browser":
        # $ A blocked browser session is replaced so the crawl continues where it stopped.$
        def make_browser() -> BrowserFetcher:
            """### Build one fresh browser session."""
            return BrowserFetcher(
                user_agent=settings.crawl_user_agent,
                timeout_seconds=request_timeout(arguments, settings),
                max_retries=retry_budget(arguments, settings),
                backoff_seconds=settings.crawl_retry_backoff_seconds,
                request_delay_seconds=request_delay(arguments, settings),
                respect_robots_txt=robots_enabled(arguments, settings),
                allowed_redirect_hosts=redirect_host_allowlist(settings),
                max_response_bytes=settings.pdf_max_bytes,
                headless=settings.browser_headless and not arguments.headed,
                wait_until=settings.browser_wait_until,
                settle_milliseconds=settings.browser_settle_milliseconds,
            )

        return RotatingFetcher(
            make_browser,
            engine_name="browser",
            max_sessions=settings.browser_max_sessions,
            blocked_session_limit=settings.browser_blocked_session_limit,
        )
    return HttpFetcher(
        user_agent=settings.crawl_user_agent,
        timeout_seconds=request_timeout(arguments, settings),
        max_retries=retry_budget(arguments, settings),
        backoff_seconds=settings.crawl_retry_backoff_seconds,
        request_delay_seconds=request_delay(arguments, settings),
        respect_robots_txt=robots_enabled(arguments, settings),
        allowed_redirect_hosts=redirect_host_allowlist(settings),
        max_response_bytes=settings.pdf_max_bytes,
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
            f"filtered={result.records_filtered_out} "
            f"missing_required={result.records_missing_required_fields} "
            f"metadata_only={result.records_without_text} "
            f"resources={result.resources_processed}/{result.resources_discovered} "
            f"resource_extracted={result.resources_extracted} unavailable={result.resources_unavailable} "
            f"failed={result.resources_failed} skipped={result.resources_skipped} "
            f"pagination_complete={result.pagination_complete}"
        )
        lines.extend(f"    error: {error}" for error in result.errors)
    return "\n".join(lines)


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


def alembic_configuration(settings: Settings) -> Config:
    """### Build an Alembic configuration bound to the application database URL."""
    from alembic.config import Config

    configuration = Config("alembic.ini")
    configuration.set_main_option("sqlalchemy.url", settings.database_url)
    return configuration


def run_db_command(arguments: argparse.Namespace, settings: Settings) -> int:
    """### Execute a database maintenance subcommand."""
    from alembic import command

    configuration = alembic_configuration(settings)
    if arguments.db_command == "upgrade":
        command.upgrade(configuration, arguments.revision)
        print(f"database upgraded to {arguments.revision}")
        return 0
    if arguments.db_command == "current":
        command.current(configuration)
        return 0
    if arguments.db_command == "revision":
        command.revision(configuration, message=arguments.message, autogenerate=False)
        print("empty revision created")
        return 0
    if arguments.db_command == "seed":
        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        try:
            with session_scope(factory) as session:
                created = seed_sources(session)
        finally:
            engine.dispose()
        print(f"source registry seeded ({created} created)")
        return 0
    print(f"unknown database command {arguments.db_command!r}")
    return 2


def persist_report(report: CrawlRunReport, settings: Settings) -> int:
    """### Persist a crawl report and report the stored run identity."""
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    try:
        persisted = persist_crawl_report(factory, report)
    finally:
        engine.dispose()
    print(f"persisted run {persisted.run_id} status={persisted.status}")
    return 0 if persisted.status == "success" else 1


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    """### Run the CLI and return a process exit code."""
    arguments = build_parser().parse_args(argv)
    settings = settings or get_settings()
    configure_logging(
        getattr(arguments, "log_level", None) or settings.log_level,
        getattr(arguments, "log_format", None) or settings.log_format,
    )

    if arguments.command == "db":
        return run_db_command(arguments, settings)

    try:
        actors = build_actors(arguments, settings)
    except ValueError as error:
        # $ Missing source locations are operator errors, so report them without a traceback.$
        print(f"configuration error: {error}")
        return 2
    # $ Sources may resolve to different engines, so each engine runs its own fetcher.$
    selected = list(SOURCE_NAMES) if arguments.source == "all" else [arguments.source]
    report = CrawlRunReport()
    fetchers: list[Fetcher] = []
    try:
        grouped: dict[str, list[Actor]] = {}
        for name, actor in zip(selected, actors, strict=True):
            grouped.setdefault(engine_for_source(name, arguments, settings), []).append(actor)
        for engine, engine_actors in grouped.items():
            fetcher = build_fetcher(arguments, settings, engine=engine)
            fetchers.append(fetcher)
            partial = run_crawl(engine_actors, fetcher)
            report.results.extend(partial.results)
            report.duration_seconds += partial.duration_seconds
    finally:
        for fetcher in fetchers:
            fetcher.close()
    for actor in actors:
        close = getattr(actor, "close", None)
        if callable(close):
            close()

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

    crawl_succeeded = report.status == "success"
    if arguments.persist:
        try:
            persist_succeeded = persist_report(report, settings) == 0
        except Exception as error:  # $ Persistence failures are reported, never hidden.$
            print(f"persistence error: {error}")
            persist_succeeded = False
        return 0 if crawl_succeeded and persist_succeeded else 1
    return 0 if crawl_succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
