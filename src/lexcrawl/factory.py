"""### Composition root: build source checks, actors, and acquisition strategies."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from lexcrawl.actors.base import Actor
from lexcrawl.actors.cba import CbaNewsReleasesActor
from lexcrawl.actors.commerce import CommerceReportsActor
from lexcrawl.actors.uae_legislation import UaeLegislationActor
from lexcrawl.checks.base import Check
from lexcrawl.checks.cba import CbaCheck
from lexcrawl.checks.commerce import CommerceCheck
from lexcrawl.checks.identities import DocumentMemory, JsonDocumentMemory
from lexcrawl.checks.uae import UaeCheck
from lexcrawl.config import Settings
from lexcrawl.crawling.base import Fetcher
from lexcrawl.crawling.direct import DirectHtmlScraper
from lexcrawl.crawling.fallback import FallbackScraper
from lexcrawl.crawling.firecrawl import FirecrawlClient
from lexcrawl.crawling.http import HttpFetcher
from lexcrawl.crawling.scrape import Scraper
from lexcrawl.db.engine import create_db_engine
from lexcrawl.repositories.memory import DbDocumentMemory

SOURCE_NAMES = ("uae", "commerce", "cba")
CHECK_SOURCES = ("uae", "commerce", "cba")

# $ Sources whose acquisition path spends provider credits (Firecrawl).$
CREDIT_SOURCES = ("commerce",)


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


def redirect_host_allowlist(settings: Settings) -> tuple[str, ...]:
    """### Allow redirects among configured source hosts and their www aliases."""
    hosts: set[str] = set()
    for value in (settings.releases_api_url, settings.releases_site_url, settings.listings_url):
        host = (urlparse(value).hostname or value).lower().rstrip(".")
        if not host:
            continue
        hosts.add(host)
        hosts.add(host[4:] if host.startswith("www.") else f"www.{host}")
    return tuple(sorted(hosts))


def build_http_fetcher(settings: Settings) -> Fetcher:
    """### Build the plain HTTP strategy from settings, for CBA and the source checks."""
    return HttpFetcher(
        user_agent=settings.crawl_user_agent,
        timeout_seconds=settings.crawl_request_timeout_seconds,
        max_retries=settings.crawl_max_retries,
        backoff_seconds=settings.crawl_retry_backoff_seconds,
        request_delay_seconds=settings.crawl_request_delay_seconds,
        max_response_bytes=settings.crawl_max_response_bytes,
        allowed_redirect_hosts=redirect_host_allowlist(settings),
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


def build_scraper(name: str, settings: Settings) -> Scraper | None:
    """### Build the acquisition strategy a source uses, if any."""
    if name == "uae":
        return build_uae_scraper(settings)
    if name == "commerce":
        return build_firecrawl(settings)
    return None


def build_document_memory(settings: Settings) -> DocumentMemory:
    """### Build the identity memory: the database by default, JSON for tests/dev."""
    if settings.state_backend == "json":
        return JsonDocumentMemory(Path(settings.identities_state_path))
    if not settings.database_url.strip():
        raise ValueError("DATABASE_URL is required when STATE_BACKEND=db; set it or use STATE_BACKEND=json")
    return DbDocumentMemory(
        create_db_engine(settings),
        attempt_cap=settings.resource_extraction_attempts,
        max_bytes=settings.resource_max_bytes,
        lease_seconds=settings.resource_claim_lease_seconds,
    )


def cba_sitemap_url(settings: Settings) -> str:
    """### Derive the CBA sitemap URL from the configured site URL."""
    site = settings.releases_site_url.strip()
    if not site:
        raise ValueError("RELEASES_SITE_URL is not configured; set it in .env (see .env.example)")
    return f"{site.rstrip('/')}/sitemap.xml"


def build_check(name: str, settings: Settings, *, scraper: Scraper | None, http_fetcher: Fetcher) -> Check:
    """### Build the change probe for one source."""
    if name == "uae":
        if scraper is None:
            raise ValueError("the UAE check requires a scraper")
        return UaeCheck(listing_url=settings.listings_url, scraper=scraper)
    if name == "commerce":
        if scraper is None:
            raise ValueError("the Commerce check requires a scraper")
        return CommerceCheck(listing_url=settings.reports_listing_url, scraper=scraper)
    if name == "cba":
        return CbaCheck(sitemap_url=cba_sitemap_url(settings), fetcher=http_fetcher)
    raise ValueError(f"unknown source {name!r}")


def build_actor(
    name: str,
    settings: Settings,
    *,
    scraper: Scraper | None,
    http_fetcher: Fetcher,
    max_pages: int | None = None,
    is_known: Callable[[str], bool] | None = None,
    max_new_items: int | None = None,
) -> Actor:
    """### Build one actor sharing a scraper and an optional known-identity predicate."""
    if name == "cba":
        return CbaNewsReleasesActor(
            api_url=settings.releases_api_url,
            site_url=settings.releases_site_url,
            api_language=settings.releases_api_language,
            type_guid=settings.releases_type_id,
            article_path=settings.releases_article_path,
            page_size=settings.releases_page_size,
            max_pages=max_pages if max_pages is not None else settings.releases_max_pages,
            is_known=is_known,
            max_new_items=max_new_items,
        )
    if name == "commerce":
        if scraper is None:
            raise ValueError("the Commerce actor requires a scraper")
        return CommerceReportsActor(
            listing_url=settings.reports_listing_url,
            firecrawl=scraper,
            max_pages=max_pages if max_pages is not None else settings.firecrawl_max_pages_per_run,
            max_detail_scrapes=settings.firecrawl_max_detail_scrapes_per_run,
            min_body_words=settings.firecrawl_min_body_words,
            is_known=is_known,
            max_new_items=max_new_items,
        )
    if name == "uae":
        if scraper is None:
            raise ValueError("the UAE actor requires a scraper")
        return UaeLegislationActor(
            listing_url=settings.listings_url,
            firecrawl=scraper,
            max_pages=max_pages if max_pages is not None else settings.crawl_max_listing_pages,
            max_detail_scrapes=settings.uae_max_detail_scrapes_per_run,
            min_body_words=settings.firecrawl_min_body_words,
            is_known=is_known,
            max_new_items=max_new_items,
        )
    raise ValueError(f"unknown source {name!r}")
