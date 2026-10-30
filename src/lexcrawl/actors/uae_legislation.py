"""### UAE Legislation news Actor: listing pagination and article extraction."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from lexcrawl.actors.base import Actor, ActorRunResult
from lexcrawl.config import required_setting
from lexcrawl.crawling.base import Fetcher, FetchError
from lexcrawl.logging import get_logger
from lexcrawl.models.document import ArticleContentError, ArticleStub, CanonicalDocument, RawArticle
from lexcrawl.pipeline.normalize import collapse_whitespace, paragraphs_of, text_of

LOGGER = get_logger(__name__)

SOURCE_NAME = "uae_legislation_news"

# $ Selectors observed in the saved listing snapshot for this source.$
CARD_SELECTOR = ".item_"
CARD_LINK_SELECTOR = "a.link_"
CARD_TITLE_SELECTOR = ".title_"
CARD_DATE_SELECTOR = ".dateBox"
CARD_SUMMARY_SELECTOR = ".desc_"
CARD_IMAGE_SELECTOR = ".img_ img"
CARD_IMAGE_SOURCE_SELECTOR = ".img_ source"
PAGINATION_SELECTOR = ".table_pagination"
NEXT_PAGE_CLASS = "next_"

# $ Selectors observed in the saved article snapshot for this source.$
DETAIL_TITLE_SELECTOR = ".section_title h2"
DETAIL_TITLE_FALLBACK_SELECTOR = ".newsDetailBox h2"
DETAIL_CONTAINER_SELECTOR = ".newsDetailBox"
DETAIL_DATE_SELECTOR = ".newsDetailBox .dateBox"
DETAIL_BODY_SELECTOR = ".newsDetailBox .textBox"
DETAIL_IMAGE_SELECTOR = ".newsDetailBox .imgBox img"

_PUBLICATION_DATE_FORMATS = ("%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class ListingPage:
    """### Parsed listing page: article stubs plus the next page link."""

    stubs: tuple[ArticleStub, ...] = ()
    next_page_url: str | None = None
    skipped_cards: int = 0
    duplicate_urls: int = 0


@dataclass(slots=True)
class _DetailOutcome:
    """### Result of fetching and parsing a single article page."""

    article: RawArticle | None = None
    error: str | None = None
    missing_required_fields: bool = False


def _soup(html: bytes | str) -> BeautifulSoup:
    """### Parse HTML bytes, letting BeautifulSoup detect the encoding."""
    return BeautifulSoup(html, "lxml")


def _resolve_url(base_url: str, reference: object) -> str | None:
    """### Resolve a possibly relative reference against the page URL."""
    if not isinstance(reference, str):
        return None
    cleaned = reference.strip()
    if not cleaned or cleaned.startswith("#") or cleaned.lower().startswith(("javascript:", "mailto:", "tel:")):
        return None
    return urljoin(base_url, cleaned)


def _has_class(tag: Tag, name: str) -> bool:
    """### Report whether a tag carries the given CSS class token."""
    classes = tag.get("class")
    if isinstance(classes, list):
        return any(str(value) == name for value in classes)
    return classes == name


def article_scope(listing_url: str) -> tuple[str, str]:
    """### Derive the article host and path prefix from the listing URL."""
    parsed = urlparse(listing_url)
    return parsed.netloc.lower(), f"{parsed.path.rstrip('/')}/"


def is_article_url(url: str, *, host: str, path_prefix: str) -> bool:
    """### Report whether a URL is an in-scope article of the configured source."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    candidate = parsed.netloc.lower()
    if candidate != host and not candidate.endswith(f".{host}"):
        return False
    return parsed.path.startswith(path_prefix) and len(parsed.path) > len(path_prefix)


def parse_published_date(value: str) -> date | None:
    """### Parse a source publication date, returning None when unusable."""
    cleaned = collapse_whitespace(value)
    if not cleaned:
        return None
    for date_format in _PUBLICATION_DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
    return None


def parse_listing(
    html: bytes | str,
    page_url: str,
    *,
    host: str | None = None,
    path_prefix: str | None = None,
) -> ListingPage:
    """### Extract article stubs and the next page link from a listing page."""
    default_host, default_prefix = article_scope(page_url)
    article_host = host if host is not None else default_host
    article_prefix = path_prefix if path_prefix is not None else default_prefix
    soup = _soup(html)
    stubs: list[ArticleStub] = []
    seen: set[str] = set()
    skipped = 0
    duplicates = 0

    for card in soup.select(CARD_SELECTOR):
        link = card.select_one(CARD_LINK_SELECTOR)
        href = _resolve_url(page_url, link.get("href") if isinstance(link, Tag) else None)
        title = text_of(card.select_one(CARD_TITLE_SELECTOR))
        if href is None or not is_article_url(href, host=article_host, path_prefix=article_prefix) or not title:
            skipped += 1
            continue
        if href in seen:
            duplicates += 1
            continue
        seen.add(href)
        stubs.append(
            ArticleStub(
                url=href,
                title=title,
                published_at=parse_published_date(text_of(card.select_one(CARD_DATE_SELECTOR))),
                summary=text_of(card.select_one(CARD_SUMMARY_SELECTOR)) or None,
                image_url=_card_image_url(card, page_url),
            )
        )

    return ListingPage(
        stubs=tuple(stubs),
        next_page_url=_next_page_url(soup, page_url),
        skipped_cards=skipped,
        duplicate_urls=duplicates,
    )


def _card_image_url(card: Tag, page_url: str) -> str | None:
    """### Resolve the listing card image URL from `img` or `source`."""
    image = card.select_one(CARD_IMAGE_SELECTOR)
    if isinstance(image, Tag):
        resolved = _resolve_url(page_url, image.get("src"))
        if resolved is not None:
            return resolved
    source = card.select_one(CARD_IMAGE_SOURCE_SELECTOR)
    if isinstance(source, Tag):
        return _resolve_url(page_url, source.get("srcset"))
    return None


def _next_page_url(soup: BeautifulSoup, page_url: str) -> str | None:
    """### Resolve the pagination 'next' link when the source provides one."""
    container = soup.select_one(PAGINATION_SELECTOR)
    if container is None:
        return None
    for anchor in container.select("a"):
        if not _has_class(anchor, NEXT_PAGE_CLASS):
            continue
        resolved = _resolve_url(page_url, anchor.get("href"))
        if resolved is None or resolved == page_url:
            return None
        return resolved
    return None


def parse_detail(html: bytes | str, url: str, *, source: str = SOURCE_NAME, language: str = "ar") -> RawArticle:
    """### Extract one article from its detail page, or raise for missing content."""
    soup = _soup(html)
    container = soup.select_one(DETAIL_CONTAINER_SELECTOR)
    if container is None:
        raise ArticleContentError(f"detail container {DETAIL_CONTAINER_SELECTOR!r} not found at {url}")

    title = text_of(soup.select_one(DETAIL_TITLE_SELECTOR)) or text_of(soup.select_one(DETAIL_TITLE_FALLBACK_SELECTOR))
    if not title:
        raise ArticleContentError(f"article title not found at {url}")

    paragraphs = paragraphs_of(soup.select_one(DETAIL_BODY_SELECTOR))
    if not paragraphs:
        raise ArticleContentError(f"article body not found at {url}")

    return RawArticle(
        source=source,
        url=url,
        title=title,
        language=language,
        published_at=parse_published_date(text_of(soup.select_one(DETAIL_DATE_SELECTOR))),
        summary=None,
        image_url=_detail_image_url(soup, url),
        paragraphs=paragraphs,
    )


def _detail_image_url(soup: BeautifulSoup, url: str) -> str | None:
    """### Resolve the article image URL when the source provides one."""
    image = soup.select_one(DETAIL_IMAGE_SELECTOR)
    if isinstance(image, Tag):
        return _resolve_url(url, image.get("src"))
    return None


class UaeLegislationActor(Actor):
    """### Crawl UAE Legislation news and emit canonical documents."""

    name = SOURCE_NAME

    def __init__(
        self,
        *,
        listing_url: str,
        language: str = "",
        max_listing_pages: int = 25,
        concurrency: int = 1,
        source_name: str = SOURCE_NAME,
    ) -> None:
        """### Configure the actor's listing entry point and crawl bounds."""
        self.listing_url = required_setting(listing_url, name="LISTINGS_URL")
        self.article_host, self.article_path_prefix = article_scope(self.listing_url)
        self.language = language
        self.max_listing_pages = max_listing_pages
        self.concurrency = max(1, concurrency)
        self.source_name = source_name
        self._logger = get_logger(f"{__name__}.{source_name}")

    def run(self, fetcher: Fetcher) -> ActorRunResult:
        """### Run listing pagination then detail extraction, reporting counters."""
        started_at = time.monotonic()
        result = ActorRunResult(source=self.source_name)
        discovered: dict[str, ArticleStub] = {}

        result.pagination_complete = self._walk_listing(fetcher, discovered, result)
        self._collect_details(fetcher, list(discovered.values()), result)

        result.duration_seconds = time.monotonic() - started_at
        self._logger.info(
            "actor_run_finished",
            extra={
                "source": self.source_name,
                "status": result.status,
                "documents": len(result.documents),
                "errors": len(result.errors),
                "pagination_complete": result.pagination_complete,
            },
        )
        return result

    def _walk_listing(self, fetcher: Fetcher, discovered: dict[str, ArticleStub], result: ActorRunResult) -> bool:
        """### Follow listing pagination, returning whether it ran to completion."""
        visited: set[str] = set()
        page_url: str | None = self.listing_url
        while page_url is not None:
            if page_url in visited:
                result.add_error(f"pagination loop detected at {page_url}")
                return False
            if result.listing_pages_attempted >= self.max_listing_pages:
                result.add_error(f"stopped after hitting max_listing_pages={self.max_listing_pages}")
                return False
            visited.add(page_url)
            result.listing_pages_attempted += 1

            try:
                page = parse_listing(
                    fetcher.fetch(page_url).content,
                    page_url,
                    host=self.article_host,
                    path_prefix=self.article_path_prefix,
                )
            except FetchError as error:
                result.listing_pages_failed += 1
                result.add_error(f"listing fetch failed at {page_url}: {error}")
                return False
            except ArticleContentError as error:
                result.listing_pages_failed += 1
                result.add_error(f"listing parse failed at {page_url}: {error}")
                return False

            result.listing_pages_succeeded += 1
            result.article_urls_deduplicated += page.duplicate_urls
            if page.skipped_cards:
                result.add_error(f"skipped {page.skipped_cards} malformed card(s) on {page_url}")
            for stub in page.stubs:
                if stub.url in discovered:
                    result.article_urls_deduplicated += 1
                    continue
                discovered[stub.url] = stub
            result.articles_discovered = len(discovered)
            page_url = page.next_page_url
        return True

    def _collect_details(self, fetcher: Fetcher, stubs: list[ArticleStub], result: ActorRunResult) -> None:
        """### Fetch and extract every discovered article, keeping listing metadata."""
        if not stubs:
            result.add_error("no article URLs discovered on the listing pages")
            return
        outcomes = self._extract_articles(fetcher, stubs)
        for stub, outcome in zip(stubs, outcomes, strict=True):
            result.detail_pages_attempted += 1
            if outcome.error:
                result.add_error(outcome.error)
            if outcome.article is None:
                result.detail_pages_failed += 1
                if outcome.missing_required_fields:
                    result.records_missing_required_fields += 1
                # $ Discovery metadata is still worth storing when the article body is unavailable.$
                result.records_without_text += 1
                result.documents.append(
                    CanonicalDocument.from_listing_entry(stub, source=self.source_name, language=self.language)
                )
                continue
            result.detail_pages_succeeded += 1
            result.documents.append(CanonicalDocument.from_raw(outcome.article))

    def _extract_articles(self, fetcher: Fetcher, stubs: list[ArticleStub]) -> list[_DetailOutcome]:
        """### Extract articles in order, staying single-threaded when unpooled."""
        if self.concurrency == 1:
            return [self._extract_article(fetcher, stub) for stub in stubs]
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            return list(pool.map(partial(self._extract_article, fetcher), stubs))

    def _extract_article(self, fetcher: Fetcher, stub: ArticleStub) -> _DetailOutcome:
        """### Fetch one article page and convert it into a raw contract record."""
        try:
            page = fetcher.fetch(stub.url)
        except FetchError as error:
            return _DetailOutcome(error=f"article fetch failed at {stub.url}: {error}")
        try:
            article = parse_detail(page.content, stub.url, source=self.source_name, language=self.language)
        except ArticleContentError as error:
            return _DetailOutcome(error=f"article parse failed at {stub.url}: {error}", missing_required_fields=True)
        if stub.summary and not article.summary:
            article.summary = stub.summary
        if stub.published_at and article.published_at is None:
            article.published_at = stub.published_at
        if stub.image_url and not article.image_url:
            article.image_url = stub.image_url
        return _DetailOutcome(article=article)
