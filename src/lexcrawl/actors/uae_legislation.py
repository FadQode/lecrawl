"""### UAE Legislation news Actor, acquiring listing and detail pages through Firecrawl."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from lexcrawl.actors.base import Actor, ActorRunResult
from lexcrawl.config import required_setting
from lexcrawl.crawling.base import redact_url
from lexcrawl.crawling.firecrawl import FirecrawlError, FirecrawlScraper
from lexcrawl.logging import get_logger
from lexcrawl.models.document import ArticleStub, CanonicalDocument, RawArticle, stable_identity
from lexcrawl.pipeline.normalize import collapse_whitespace, paragraphs_of, text_of

LOGGER = get_logger(__name__)

SOURCE_NAME = "uae_legislation_news"
OUTPUT_LANGUAGE = "ar"

# $ Selectors observed in the source's listing markup.$
CARD_SELECTOR = ".item_"
CARD_LINK_SELECTOR = "a.link_"
CARD_TITLE_SELECTOR = ".title_"
CARD_DATE_SELECTOR = ".dateBox"
CARD_SUMMARY_SELECTOR = ".desc_"
CARD_IMAGE_SELECTOR = ".img_ img"
CARD_IMAGE_SOURCE_SELECTOR = ".img_ source"
PAGINATION_SELECTOR = ".table_pagination"

# $ Selectors observed in the source's article markup.$
DETAIL_TITLE_SELECTOR = ".section_title h2"
DETAIL_TITLE_FALLBACK_SELECTOR = ".newsDetailBox h2"
DETAIL_DATE_SELECTOR = ".newsDetailBox .dateBox"
DETAIL_BODY_SELECTOR = ".newsDetailBox .textBox"
DETAIL_IMAGE_SELECTOR = ".newsDetailBox .imgBox img"

_DATE_FORMATS = ("%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d")


class UaeExtractionError(ValueError):
    """### Raised when a UAE page cannot be turned into a usable record."""


class InsufficientArticleContent(UaeExtractionError):
    """### Raised when an article page has no meaningful body content."""

    def __init__(self, message: str, *, article: RawArticle, words: int) -> None:
        """### Keep the parsed article so its metadata can still be stored."""
        super().__init__(message)
        self.article = article
        self.words = words


@dataclass(frozen=True, slots=True)
class ListingPage:
    """### Parsed listing page: article stubs plus the next page link."""

    stubs: tuple[ArticleStub, ...] = ()
    next_page_url: str | None = None
    skipped_cards: int = 0
    duplicate_urls: int = 0


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
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
    return None


def _resolve_url(base_url: str, reference: object) -> str | None:
    """### Resolve a possibly relative reference against the page URL."""
    if not isinstance(reference, str):
        return None
    cleaned = reference.strip()
    if not cleaned or cleaned.startswith("#") or cleaned.lower().startswith(("javascript:", "mailto:", "tel:")):
        return None
    return urljoin(base_url, cleaned)


def _has_next_class(tag: Tag) -> bool:
    """### Report whether a tag carries a CSS class token naming the next control."""
    classes = tag.get("class")
    if isinstance(classes, list):
        return any("next" in str(token).lower() for token in classes)
    return isinstance(classes, str) and "next" in classes.lower()


def _first_srcset_url(value: object) -> str | None:
    """### Return the first URL token of a `srcset` attribute."""
    if not isinstance(value, str):
        return None
    first = value.split(",", 1)[0].strip()
    return first.split(" ", 1)[0] or None


def _card_image_url(card: Tag, page_url: str) -> str | None:
    """### Resolve the listing card image URL from `img` or `source`."""
    image = card.select_one(CARD_IMAGE_SELECTOR)
    if isinstance(image, Tag):
        resolved = _resolve_url(page_url, image.get("src"))
        if resolved is not None:
            return resolved
    source = card.select_one(CARD_IMAGE_SOURCE_SELECTOR)
    if isinstance(source, Tag):
        return _resolve_url(page_url, _first_srcset_url(source.get("srcset")) or source.get("src"))
    return None


def _next_page_url(soup: BeautifulSoup, page_url: str) -> str | None:
    """### Read the source's own next-page control."""
    pagination = soup.select_one(PAGINATION_SELECTOR)
    if pagination is None:
        return None
    for anchor in pagination.select("a[href]"):
        if _has_next_class(anchor):
            return _resolve_url(page_url, anchor.get("href"))
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
    soup = BeautifulSoup(html, "lxml")
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


def parse_detail(html: bytes | str, *, url: str, min_body_words: int) -> RawArticle:
    """### Parse one article page, flagging a thin body as incomplete."""
    if not html.strip():
        raise UaeExtractionError(f"detail page is empty at {url}")
    soup = BeautifulSoup(html, "lxml")
    title = (
        text_of(soup.select_one(DETAIL_TITLE_SELECTOR))
        or text_of(soup.select_one(DETAIL_TITLE_FALLBACK_SELECTOR))
        or text_of(soup.select_one("h1"))
    )
    if not title:
        raise UaeExtractionError(f"detail page has no article title at {url}")

    body = soup.select_one(DETAIL_BODY_SELECTOR)
    if body is None:
        raise UaeExtractionError(f"detail page has no body container at {url}")
    paragraphs = paragraphs_of(body)
    if not paragraphs:
        raise UaeExtractionError(f"detail page body is empty at {url}")

    date_node = soup.select_one(DETAIL_DATE_SELECTOR)
    raw_date = text_of(date_node) or None
    image_node = soup.select_one(DETAIL_IMAGE_SELECTOR)
    image_url = _resolve_url(url, image_node.get("src") if isinstance(image_node, Tag) else None)

    article = RawArticle(
        source=SOURCE_NAME,
        url=url,
        title=title,
        language=OUTPUT_LANGUAGE,
        published_at=parse_published_date(raw_date or ""),
        published_date_raw=raw_date,
        image_url=image_url,
        paragraphs=paragraphs,
    )
    words = len(" ".join(paragraphs).split())
    if words < min_body_words:
        raise InsufficientArticleContent(
            f"article body has {words} word(s), below the {min_body_words}-word minimum",
            article=article,
            words=words,
        )
    return article


class UaeLegislationActor(Actor):
    """### Collect UAE Legislation news by acquiring every page through Firecrawl."""

    name = SOURCE_NAME

    def __init__(
        self,
        *,
        listing_url: str,
        firecrawl: FirecrawlScraper | None,
        max_pages: int = 25,
        max_detail_scrapes: int = 20,
        min_body_words: int = 30,
        source_name: str = SOURCE_NAME,
        is_known: Callable[[str], bool] | None = None,
        max_new_items: int | None = None,
    ) -> None:
        """### Configure the listing entry point, budgets, and the Firecrawl scraper."""
        self.listing_url = required_setting(listing_url, name="LISTINGS_URL")
        self.article_host, self.article_prefix = article_scope(self.listing_url)
        self.firecrawl = firecrawl
        self.max_pages = max(1, max_pages)
        self.max_detail_scrapes = max(0, max_detail_scrapes)
        self.min_body_words = max(1, min_body_words)
        self.source_name = source_name
        self._is_known = is_known
        self._max_new_items = max_new_items
        self._logger = get_logger(f"{__name__}.{source_name}")

    def close(self) -> None:
        """### Release the Firecrawl client when it owns resources."""
        closer = getattr(self.firecrawl, "close", None)
        if callable(closer):
            closer()

    def run(self, fetcher: object) -> ActorRunResult:
        """### Page through the listing, then acquire each new article through Firecrawl."""
        started_at = time.monotonic()
        result = ActorRunResult(source=self.source_name)
        if self.firecrawl is None:
            result.add_error("firecrawl is not configured; the UAE source cannot be acquired")
            return result

        try:
            stubs, complete = self._discover(result)
            result.articles_discovered = len(stubs)
            self._acquire(stubs, result)
        except FirecrawlError as error:
            # $ A provider-wide failure stops the run and is reported, never masked as empty.$
            self._logger.exception("uae_firecrawl_failed", extra={"source": self.source_name})
            result.add_error(f"firecrawl failed: {error}")
            complete = False
        except Exception as error:
            self._logger.exception("uae_actor_failed", extra={"source": self.source_name})
            result.add_error(f"unexpected actor failure: {error!r}")
            complete = False

        result.pagination_complete = complete
        result.duration_seconds = time.monotonic() - started_at
        self._logger.info(
            "actor_run_finished",
            extra={
                "source": self.source_name,
                "status": result.status,
                "documents": len(result.documents),
                "pending": result.documents_pending,
                "credits": result.provider_credits_used,
                "pagination_complete": result.pagination_complete,
            },
        )
        return result

    def _discover(self, result: ActorRunResult) -> tuple[dict[str, ArticleStub], bool]:
        """### Follow the source's own next-page links until the end, a loop, or the bound."""
        collected: dict[str, ArticleStub] = {}
        visited: set[str] = set()
        page_url: str | None = self.listing_url
        for _ in range(self.max_pages):
            if page_url is None:
                return collected, True
            if page_url in visited:
                result.add_error(f"listing page loop detected at {redact_url(page_url)}")
                return collected, False
            visited.add(page_url)
            page = self._scrape_listing(page_url, result)
            if page is None:
                return collected, False
            result.records_filtered_out += page.skipped_cards
            result.article_urls_deduplicated += page.duplicate_urls
            for stub in page.stubs:
                if stub.url in collected:
                    result.article_urls_deduplicated += 1
                    continue
                collected[stub.url] = stub
            page_url = page.next_page_url
            if page_url is None:
                return collected, True
        else:
            result.add_error(f"stopped after hitting max_pages={self.max_pages}")
            return collected, False

    def _scrape_listing(self, page_url: str, result: ActorRunResult) -> ListingPage | None:
        """### Scrape one listing page through Firecrawl and parse its cards."""
        result.listing_pages_attempted += 1
        assert self.firecrawl is not None
        try:
            scraped = self.firecrawl.scrape(page_url)
        except FirecrawlError as error:
            result.listing_pages_failed += 1
            result.add_error(f"firecrawl listing failed at {redact_url(page_url)}: {error}")
            return None
        result.provider_credits_used += scraped.credits_used
        if scraped.target_status is not None and scraped.target_status >= 400:
            result.listing_pages_failed += 1
            result.add_error(f"listing page {redact_url(page_url)} returned HTTP {scraped.target_status}")
            return None
        page = parse_listing(
            scraped.html,
            page_url,
            host=self.article_host,
            path_prefix=self.article_prefix,
        )
        if not page.stubs:
            result.listing_pages_failed += 1
            result.add_error(f"listing page {redact_url(page_url)} returned no article cards")
            return None
        result.listing_pages_succeeded += 1
        return page

    def _acquire(self, stubs: dict[str, ArticleStub], result: ActorRunResult) -> None:
        """### Scrape a detail page for each new article, within the detail budget."""
        pending = [stub for stub in stubs.values() if not self._is_known_stub(stub)]
        result.records_skipped_known += len(stubs) - len(pending)
        if self._max_new_items is not None and len(pending) > self._max_new_items:
            result.anomaly_detected = True
            result.add_error(
                f"anomaly: {len(pending)} new article(s) exceeds the {self._max_new_items} item limit; crawl skipped"
            )
            return
        for index, stub in enumerate(pending):
            if index >= self.max_detail_scrapes:
                result.documents_pending = len(pending) - index
                result.add_error(
                    f"detail scrape budget reached; {result.documents_pending} article(s) pending for a later run"
                )
                return
            result.detail_pages_attempted += 1
            self._acquire_one(stub, result)

    def _is_known_stub(self, stub: ArticleStub) -> bool:
        """### Report whether an article identity was already ingested."""
        return self._is_known is not None and self._is_known(stable_identity(self.source_name, None, stub.url))

    def _acquire_one(self, stub: ArticleStub, result: ActorRunResult) -> None:
        """### Acquire and validate one article, isolating its failures."""
        assert self.firecrawl is not None
        try:
            scraped = self.firecrawl.scrape(stub.url, cache=True)
        except FirecrawlError as error:
            self._fail(stub, result, f"firecrawl detail failed: {error}")
            return
        result.provider_credits_used += scraped.credits_used
        if scraped.target_status is not None and scraped.target_status >= 400:
            self._fail(stub, result, f"detail page returned HTTP {scraped.target_status}")
            return
        try:
            article = parse_detail(scraped.html, url=stub.url, min_body_words=self.min_body_words)
        except InsufficientArticleContent as error:
            # $ The page parsed, so its metadata is still worth keeping.$
            self._store(error.article, stub, result, error=str(error))
            return
        except UaeExtractionError as error:
            self._fail(stub, result, f"detail extraction failed: {error}")
            return
        self._store(article, stub, result)

    def _store(
        self,
        article: RawArticle,
        stub: ArticleStub,
        result: ActorRunResult,
        *,
        error: str | None = None,
    ) -> None:
        """### Canonicalize one article, filling gaps from its listing card."""
        article = article.model_copy(
            update={
                "summary": article.summary or stub.summary,
                "image_url": article.image_url or stub.image_url,
                "published_at": article.published_at or stub.published_at,
            }
        )
        document = CanonicalDocument.from_raw(article)
        if error is not None:
            result.detail_pages_failed += 1
            result.records_without_text += 1
            result.records_missing_required_fields += 1
            result.add_error(f"{redact_url(stub.url)}: {error}")
            # $ An incomplete body is dropped so a failed record never carries partial text.$
            document = document.model_copy(
                update={"text": None, "content_hash": None, "extraction_status": "failed", "extraction_error": error}
            )
        else:
            result.detail_pages_succeeded += 1
        result.documents.append(document)

    def _fail(self, stub: ArticleStub, result: ActorRunResult, message: str) -> None:
        """### Record an unusable article while preserving its discovered metadata."""
        result.detail_pages_failed += 1
        result.records_missing_required_fields += 1
        result.records_without_text += 1
        result.add_error(f"{redact_url(stub.url)}: {message}")
        document = CanonicalDocument.from_listing_entry(
            stub,
            source=self.source_name,
            language=OUTPUT_LANGUAGE,
            extraction_status="failed",
            extraction_error=message,
        )
        result.documents.append(document)
