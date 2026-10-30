"""### Command line entry point for running LexCrawl source actors."""

from __future__ import annotations

import argparse
import json
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from lexcrawl.actors.base import Actor
from lexcrawl.actors.cba import CbaNewsReleasesActor
from lexcrawl.actors.commerce import CommerceReportsActor
from lexcrawl.actors.uae_legislation import UaeLegislationActor
from lexcrawl.config import Settings, get_settings, split_list
from lexcrawl.crawling.base import Fetcher
from lexcrawl.crawling.browser import BrowserFetcher
from lexcrawl.crawling.http import HttpFetcher
from lexcrawl.logging import configure_logging, get_logger
from lexcrawl.orchestrator import CrawlRunReport, run_crawl

LOG = get_logger(__name__)

SOURCE_NAMES = ("uae", "commerce", "cba")


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
    return parser


def crawl_engine(arguments: argparse.Namespace, settings: Settings) -> str:
    """### Resolve the crawling strategy from the CLI or settings."""
    return arguments.engine or settings.crawl_engine


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
            concurrency=concurrency(arguments, settings),
            bilingual=settings.crawl_bilingual or arguments.bilingual,
            secondary_listing_url=settings.listings_secondary_url,
        )
    if name == "commerce":
        return CommerceReportsActor(
            api_url=settings.reports_api_url,
            api_key=settings.reports_api_key,
            article_host=settings.reports_article_host,
            article_paths=split_list(settings.reports_article_paths),
            news_types=split_list(settings.reports_news_types),
            max_pages=arguments.max_pages if arguments.max_pages is not None else settings.reports_max_pages,
            page_size=settings.reports_page_size,
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


def concurrency(arguments: argparse.Namespace, settings: Settings) -> int:
    """### Resolve detail concurrency, forcing a single thread for the browser."""
    if crawl_engine(arguments, settings) == "browser":
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


def build_fetcher(arguments: argparse.Namespace, settings: Settings) -> Fetcher:
    """### Build the crawling strategy selected by the CLI or settings."""
    if crawl_engine(arguments, settings) == "browser":
        return BrowserFetcher(
            user_agent=settings.crawl_user_agent,
            timeout_seconds=request_timeout(arguments, settings),
            max_retries=retry_budget(arguments, settings),
            backoff_seconds=settings.crawl_retry_backoff_seconds,
            request_delay_seconds=request_delay(arguments, settings),
            respect_robots_txt=robots_enabled(arguments, settings),
            headless=settings.browser_headless and not arguments.headed,
            wait_until=settings.browser_wait_until,
            settle_milliseconds=settings.browser_settle_milliseconds,
        )
    return HttpFetcher(
        user_agent=settings.crawl_user_agent,
        timeout_seconds=request_timeout(arguments, settings),
        max_retries=retry_budget(arguments, settings),
        backoff_seconds=settings.crawl_retry_backoff_seconds,
        request_delay_seconds=request_delay(arguments, settings),
        respect_robots_txt=robots_enabled(arguments, settings),
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


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    """### Run the CLI and return a process exit code."""
    arguments = build_parser().parse_args(argv)
    settings = settings or get_settings()
    configure_logging(arguments.log_level or settings.log_level, arguments.log_format or settings.log_format)

    try:
        actors = build_actors(arguments, settings)
    except ValueError as error:
        # $ Missing source locations are operator errors, so report them without a traceback.$
        print(f"configuration error: {error}")
        return 2
    with closing(build_fetcher(arguments, settings)) as fetcher:
        report = run_crawl(actors, fetcher)

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


if __name__ == "__main__":
    raise SystemExit(main())
