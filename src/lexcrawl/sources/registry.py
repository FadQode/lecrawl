"""### Source registry: one entry per source, so adding a source is a single edit.

Each :class:`SourceSpec` knows how to build that source's scraper, change probe, and actor.
The composition root (``factory``) looks sources up here by name; nothing else needs to
change when a source is added or removed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlparse

from lexcrawl.actors.base import Actor, KnownFilter
from lexcrawl.actors.cba import CbaNewsReleasesActor
from lexcrawl.actors.commerce import CommerceReportsActor
from lexcrawl.actors.uae_legislation import UaeLegislationActor
from lexcrawl.checks.base import Check
from lexcrawl.checks.cba import CbaCheck
from lexcrawl.checks.commerce import CommerceCheck
from lexcrawl.checks.uae import UaeCheck
from lexcrawl.config import Settings
from lexcrawl.crawling.base import Fetcher
from lexcrawl.crawling.budgeted import BudgetedScraper, BudgetLike
from lexcrawl.crawling.direct import DirectHtmlScraper
from lexcrawl.crawling.fallback import FallbackScraper
from lexcrawl.crawling.firecrawl import FirecrawlClient
from lexcrawl.crawling.scrape import Scraper


@dataclass(slots=True)
class BuildContext:
    """### Everything a source spec needs to build its scraper, check, or actor."""

    settings: Settings
    scraper: Scraper | None = None
    http_fetcher: Fetcher | None = None
    max_pages: int | None = None
    is_known: KnownFilter | None = None
    max_new_items: int | None = None
    budget: BudgetLike | None = None
    period: str | None = None
    cap: int = 0


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """### The builders, DB identity, and host allowlist for one source."""

    name: str
    code: str
    display_name: str
    base_url: str | None
    build_scraper: Callable[[BuildContext], Scraper | None]
    build_check: Callable[[BuildContext], Check]
    build_actor: Callable[[BuildContext], Actor]
    redirect_hosts: Callable[[Settings], Sequence[str]] = field(default=lambda settings: ())


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


def _host_aliases(*values: str) -> tuple[str, ...]:
    """### Expand configured hosts into their bare and www aliases."""
    hosts: set[str] = set()
    for value in values:
        host = (urlparse(value).hostname or value).lower().rstrip(".")
        if not host:
            continue
        hosts.add(host)
        hosts.add(host[4:] if host.startswith("www.") else f"www.{host}")
    return tuple(sorted(hosts))


def uae_allowed_hosts(settings: Settings) -> tuple[str, ...]:
    """### Allow direct fetches among the UAE listing host and its www alias."""
    return _host_aliases(settings.listings_url)


def cba_sitemap_url(settings: Settings) -> str:
    """### Derive the CBA sitemap URL from the configured site URL."""
    site = settings.releases_site_url.strip()
    if not site:
        raise ValueError("RELEASES_SITE_URL is not configured; set it in .env (see .env.example)")
    return f"{site.rstrip('/')}/sitemap.xml"


def _budgeted(scraper: Scraper, ctx: BuildContext) -> Scraper:
    """### Wrap a paid scraper in the monthly budget when a budget is available."""
    if ctx.budget is not None and ctx.period is not None:
        return BudgetedScraper(scraper, budget=ctx.budget, period=ctx.period, cap=ctx.cap)
    return scraper


def _build_uae_scraper(ctx: BuildContext) -> Scraper:
    """### UAE: direct fetch when unchallenged, budgeted Firecrawl as the fallback."""
    firecrawl = _budgeted(build_firecrawl(ctx.settings), ctx)
    if not ctx.settings.uae_direct_fetch:
        return firecrawl
    direct = DirectHtmlScraper(
        user_agent=ctx.settings.crawl_user_agent,
        timeout_seconds=ctx.settings.crawl_request_timeout_seconds,
        retry_delay_seconds=ctx.settings.crawl_retry_backoff_seconds,
        allowed_hosts=uae_allowed_hosts(ctx.settings),
    )
    return FallbackScraper(primary=direct, fallback=firecrawl)


def _build_commerce_scraper(ctx: BuildContext) -> Scraper:
    """### Commerce: Firecrawl only (plain HTTP is refused with a challenge)."""
    return _budgeted(build_firecrawl(ctx.settings), ctx)


def _build_uae_check(ctx: BuildContext) -> Check:
    """### Build the UAE change probe."""
    if ctx.scraper is None:
        raise ValueError("the UAE check requires a scraper")
    return UaeCheck(listing_url=ctx.settings.listings_url, scraper=ctx.scraper)


def _build_commerce_check(ctx: BuildContext) -> Check:
    """### Build the Commerce change probe."""
    if ctx.scraper is None:
        raise ValueError("the Commerce check requires a scraper")
    return CommerceCheck(listing_url=ctx.settings.reports_listing_url, scraper=ctx.scraper)


def _build_cba_check(ctx: BuildContext) -> Check:
    """### Build the CBA change probe (sitemap over plain HTTP)."""
    if ctx.http_fetcher is None:
        raise ValueError("the CBA check requires an HTTP fetcher")
    return CbaCheck(sitemap_url=cba_sitemap_url(ctx.settings), fetcher=ctx.http_fetcher)


def _build_cba_actor(ctx: BuildContext) -> Actor:
    """### Build the CBA releases actor."""
    return CbaNewsReleasesActor(
        api_url=ctx.settings.releases_api_url,
        site_url=ctx.settings.releases_site_url,
        api_language=ctx.settings.releases_api_language,
        type_guid=ctx.settings.releases_type_id,
        article_path=ctx.settings.releases_article_path,
        page_size=ctx.settings.releases_page_size,
        max_pages=ctx.max_pages if ctx.max_pages is not None else ctx.settings.releases_max_pages,
        is_known=ctx.is_known,
        max_new_items=ctx.max_new_items,
    )


def _build_commerce_actor(ctx: BuildContext) -> Actor:
    """### Build the Commerce reports actor."""
    if ctx.scraper is None:
        raise ValueError("the Commerce actor requires a scraper")
    return CommerceReportsActor(
        listing_url=ctx.settings.reports_listing_url,
        firecrawl=ctx.scraper,
        max_pages=ctx.max_pages if ctx.max_pages is not None else ctx.settings.firecrawl_max_pages_per_run,
        max_detail_scrapes=ctx.settings.firecrawl_max_detail_scrapes_per_run,
        min_body_words=ctx.settings.firecrawl_min_body_words,
        is_known=ctx.is_known,
        max_new_items=ctx.max_new_items,
    )


def _build_uae_actor(ctx: BuildContext) -> Actor:
    """### Build the UAE legislation actor."""
    if ctx.scraper is None:
        raise ValueError("the UAE actor requires a scraper")
    return UaeLegislationActor(
        listing_url=ctx.settings.listings_url,
        firecrawl=ctx.scraper,
        max_pages=ctx.max_pages if ctx.max_pages is not None else ctx.settings.crawl_max_listing_pages,
        max_detail_scrapes=ctx.settings.uae_max_detail_scrapes_per_run,
        min_body_words=ctx.settings.firecrawl_min_body_words,
        is_known=ctx.is_known,
        max_new_items=ctx.max_new_items,
    )


# $ Adding a source is a single entry here: its DB code, scraper, check, actor, and redirect hosts.$
REGISTRY: dict[str, SourceSpec] = {
    "uae": SourceSpec(
        name="uae",
        code="uae_legislation_news",
        display_name="UAE Legislation news",
        base_url="https://uaelegislation.gov.ae",
        build_scraper=_build_uae_scraper,
        build_check=_build_uae_check,
        build_actor=_build_uae_actor,
        redirect_hosts=lambda settings: _host_aliases(settings.listings_url),
    ),
    "commerce": SourceSpec(
        name="commerce",
        code="commerce_reports",
        display_name="U.S. Department of Commerce reports",
        base_url="https://www.commerce.gov",
        build_scraper=_build_commerce_scraper,
        build_check=_build_commerce_check,
        build_actor=_build_commerce_actor,
        redirect_hosts=lambda settings: _host_aliases(settings.reports_listing_url),
    ),
    "cba": SourceSpec(
        name="cba",
        code="cba_news_releases",
        display_name="CBA news releases",
        base_url="https://cba.ca",
        build_scraper=lambda ctx: None,
        build_check=_build_cba_check,
        build_actor=_build_cba_actor,
        redirect_hosts=lambda settings: _host_aliases(settings.releases_api_url, settings.releases_site_url),
    ),
}

SOURCE_NAMES: tuple[str, ...] = tuple(REGISTRY)
CHECK_SOURCES: tuple[str, ...] = tuple(REGISTRY)


def spec_for(name: str) -> SourceSpec:
    """### Return the spec for a source, failing with an actionable error."""
    spec = REGISTRY.get(name)
    if spec is None:
        raise ValueError(f"unknown source {name!r}")
    return spec


def redirect_host_allowlist(settings: Settings) -> tuple[str, ...]:
    """### Aggregate every registered source's redirect hosts."""
    hosts: set[str] = set()
    for spec in REGISTRY.values():
        hosts.update(spec.redirect_hosts(settings))
    return tuple(sorted(hosts))


def build_scraper(
    name: str,
    settings: Settings,
    *,
    budget: BudgetLike | None = None,
    period: str | None = None,
    cap: int = 0,
) -> Scraper | None:
    """### Build the acquisition strategy a source uses, if any (budget-aware)."""
    spec = spec_for(name)
    return spec.build_scraper(BuildContext(settings, budget=budget, period=period, cap=cap))


def build_check(name: str, settings: Settings, *, scraper: Scraper | None, http_fetcher: Fetcher) -> Check:
    """### Build the change probe for one source."""
    spec = spec_for(name)
    return spec.build_check(BuildContext(settings, scraper=scraper, http_fetcher=http_fetcher))


def build_actor(
    name: str,
    settings: Settings,
    *,
    scraper: Scraper | None,
    http_fetcher: Fetcher,
    max_pages: int | None = None,
    is_known: KnownFilter | None = None,
    max_new_items: int | None = None,
) -> Actor:
    """### Build one actor for a source."""
    spec = spec_for(name)
    return spec.build_actor(
        BuildContext(
            settings,
            scraper=scraper,
            http_fetcher=http_fetcher,
            max_pages=max_pages,
            is_known=is_known,
            max_new_items=max_new_items,
        )
    )
