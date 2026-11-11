"""### Command line entry point for running LexCrawl source actors."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from lexcrawl.actors.base import Actor
from lexcrawl.actors.cba import CbaNewsReleasesActor
from lexcrawl.actors.commerce import CommerceReportsActor
from lexcrawl.actors.uae_legislation import UaeLegislationActor
from lexcrawl.config import Settings, get_settings
from lexcrawl.crawling.base import Fetcher
from lexcrawl.crawling.direct import DirectHtmlScraper
from lexcrawl.crawling.fallback import FallbackScraper
from lexcrawl.crawling.firecrawl import FirecrawlClient
from lexcrawl.crawling.http import HttpFetcher
from lexcrawl.crawling.scrape import Scraper
from lexcrawl.logging import configure_logging, get_logger
from lexcrawl.orchestrator import CrawlRunReport, run_crawl

LOG = get_logger(__name__)

SOURCE_NAMES = ("uae", "commerce", "cba")


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
    crawl.add_argument("--log-level", default=None)
    crawl.add_argument("--log-format", choices=["json", "console"], default=None)
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


def uae_allowed_hosts(settings: Settings) -> tuple[str, ...]:
    """### Allow direct fetches among the UAE listing host and its www alias."""
    host = (urlparse(settings.listings_url).hostname or "").lower().rstrip(".")
    if not host:
        return ()
    return tuple(sorted({host, host[4:] if host.startswith("www.") else f"www.{host}"}))


def build_uae_scraper(settings: Settings) -> Scraper:
    """### Build the UAE acquisition path: direct when unchallenged, Firecrawl as the fallback."""
    firecrawl = build_firecrawl(settings)
    if not settings.uae_direct_fetch:
        return firecrawl
    direct = DirectHtmlScraper(
        user_agent=settings.crawl_user_agent,
        timeout_seconds=settings.crawl_request_timeout_seconds,
        retry_delay_seconds=settings.crawl_retry_backoff_seconds,
        allowed_hosts=uae_allowed_hosts(settings),
    )
    return FallbackScraper(primary=direct, fallback=firecrawl)


def build_actor(name: str, arguments: argparse.Namespace, settings: Settings) -> Actor:
    """### Build one actor from CLI overrides and validated settings."""
    if name == "cba":
        return CbaNewsReleasesActor(
            api_url=settings.releases_api_url,
            site_url=settings.releases_site_url,
            api_language=settings.releases_api_language,
            type_guid=settings.releases_type_id,
            article_path=settings.releases_article_path,
            page_size=settings.releases_page_size,
            max_pages=arguments.max_pages if arguments.max_pages is not None else settings.releases_max_pages,
        )
    if name == "commerce":
        # $ Commerce reports are acquired through Firecrawl, bounded by explicit budgets.$
        return CommerceReportsActor(
            listing_url=settings.reports_listing_url,
            firecrawl=build_firecrawl(settings),
            max_pages=arguments.max_pages if arguments.max_pages is not None else settings.firecrawl_max_pages_per_run,
            max_detail_scrapes=settings.firecrawl_max_detail_scrapes_per_run,
            min_body_words=settings.firecrawl_min_body_words,
        )
    if name == "uae":
        # $ UAE is fetched directly when unchallenged; Firecrawl is the fail-closed fallback.$
        return UaeLegislationActor(
            listing_url=settings.listings_url,
            firecrawl=build_uae_scraper(settings),
            max_pages=arguments.max_pages if arguments.max_pages is not None else settings.crawl_max_listing_pages,
            max_detail_scrapes=settings.uae_max_detail_scrapes_per_run,
            min_body_words=settings.firecrawl_min_body_words,
        )
    raise ValueError(f"unknown source {name!r}")


def redirect_host_allowlist(settings: Settings) -> tuple[str, ...]:
    """### Allow redirects among configured source hosts and their www aliases."""
    hosts: set[str] = set()
    for value in (settings.releases_api_url, settings.releases_site_url):
        host = (urlparse(value).hostname or value).lower().rstrip(".")
        if not host:
            continue
        hosts.add(host)
        hosts.add(host[4:] if host.startswith("www.") else f"www.{host}")
    return tuple(sorted(hosts))


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
            f"filtered={result.records_filtered_out} metadata_only={result.records_without_text} "
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
    configure_logging(
        getattr(arguments, "log_level", None) or settings.log_level,
        getattr(arguments, "log_format", None) or settings.log_format,
    )

    try:
        actor = build_actor(arguments.source, arguments, settings)
    except ValueError as error:
        # $ Missing source locations are operator errors, so report them without a traceback.$
        print(f"configuration error: {error}")
        return 2

    fetcher = build_fetcher(arguments, settings)
    try:
        report = run_crawl([actor], fetcher)
    finally:
        fetcher.close()

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
