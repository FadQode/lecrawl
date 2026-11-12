"""### U.S. Department of Commerce reports Actor, acquiring pages through Firecrawl."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from lexcrawl.actors.base import Actor, ActorRunResult
from lexcrawl.config import required_setting
from lexcrawl.crawling.base import redact_url
from lexcrawl.crawling.firecrawl import FirecrawlError, FirecrawlScraper
from lexcrawl.logging import get_logger
from lexcrawl.models.document import (
    ArticleStub,
    CanonicalDocument,
    DocumentResource,
    RawArticle,
    ResourceKind,
    stable_identity,
)
from lexcrawl.pipeline.normalize import text_of

LOGGER = get_logger(__name__)

SOURCE_NAME = "commerce_reports"
OUTPUT_LANGUAGE = "en"

RANGE_PATTERN = re.compile(r"Displaying\s+([\d,]+)\s*-\s*([\d,]+)\s+of\s+([\d,]+)")
ATTACHMENT_EXTENSIONS: dict[str, ResourceKind] = {
    "pdf": "pdf",
    "doc": "word",
    "docx": "word",
    "xls": "excel",
    "xlsx": "excel",
    "csv": "csv",
}
BODY_BLOCK_TAGS = ("p", "li", "blockquote")
_LISTING_DATE_FORMATS = ("%B %Y", "%b %Y")


class CommerceExtractionError(ValueError):
    """### Raised when a Commerce page cannot be turned into a usable record."""


class InsufficientReportContent(CommerceExtractionError):
    """### Raised when a report page has no meaningful body content."""

    def __init__(self, message: str, *, article: RawArticle, words: int) -> None:
        """### Keep the parsed article so its metadata can still be stored."""
        super().__init__(message)
        self.article = article
        self.words = words


@dataclass(frozen=True, slots=True)
class ListingDate:
    """### Parsed publication date from a month-year listing value."""

    raw: str | None = None
    month: int | None = None
    year: int | None = None


@dataclass(frozen=True, slots=True)
class ListingEntry:
    """### One report row discovered on a Commerce reports listing page."""

    identity: str
    url: str
    title: str
    published_date_raw: str | None
    published_month: int | None
    published_year: int | None
    resources: tuple[DocumentResource, ...] = ()


@dataclass(frozen=True, slots=True)
class ListingPage:
    """### One parsed listing page with its source range indicator."""

    page_number: int
    entries: tuple[ListingEntry, ...]
    total_reports: int | None = None
    range_start: int | None = None
    range_end: int | None = None


def build_page_url(listing_url: str, page_number: int) -> str:
    """### Build the explicit, validated page URL the listing uses for pagination."""
    path = urlparse(listing_url).path
    return f"{listing_url}?q={path}&page={page_number}"


def resource_kind(url: str) -> ResourceKind:
    """### Classify a linked resource by its file extension."""
    path = urlparse(url).path
    extension = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return ATTACHMENT_EXTENSIONS.get(extension, "other")


def filename_from_url(url: str | None) -> str | None:
    """### Return the file name of a resource URL when present."""
    if not url:
        return None
    name = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    return name or None


def is_report_url(url: str, *, host: str, path_prefixes: tuple[str, ...]) -> bool:
    """### Report whether a record URL is in scope for the configured source."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    candidate = parsed.netloc.lower()
    if candidate != host and not candidate.endswith(f".{host}"):
        return False
    return any(parsed.path.startswith(prefix) and len(parsed.path) > len(prefix) for prefix in path_prefixes)


def parse_listing_date(value: object) -> ListingDate:
    """### Parse a listing month-year value without inventing a day."""
    raw = " ".join(value.split()) if isinstance(value, str) else ""
    if not raw:
        return ListingDate()
    for date_format in _LISTING_DATE_FORMATS:
        try:
            parsed = datetime.strptime(raw, date_format)
        except ValueError:
            continue
        return ListingDate(raw=raw, month=parsed.month, year=parsed.year)
    return ListingDate(raw=raw)


def _content(node: Tag | None) -> Tag | None:
    """### Return the Drupal cell content wrapper when present."""
    if node is None:
        return None
    return node.select_one(".tablesaw-cell-content") or node


def _range_indicator(soup: BeautifulSoup) -> tuple[int | None, int | None, int | None]:
    """### Read the source's "Displaying X - Y of Z" range indicator."""
    header = soup.select_one("div.view-header")
    match = RANGE_PATTERN.search(text_of(header)) if header is not None else None
    if match is None:
        match = RANGE_PATTERN.search(soup.get_text(" ", strip=True))
    if match is None:
        return None, None, None
    start, end, total = (int(group.replace(",", "")) for group in match.groups())
    return start, end, total


def _attachment_links(cell: Tag | None, page_url: str) -> tuple[DocumentResource, ...]:
    """### Collect attachment links from one File(s) cell."""
    if cell is None:
        return ()
    resources: list[DocumentResource] = []
    seen: set[str] = set()
    for anchor in cell.select("a[href]"):
        href = anchor.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        url = urljoin(page_url, href)
        kind = resource_kind(url)
        if kind == "other" or url in seen:
            continue
        seen.add(url)
        title = anchor.get("title")
        label = title if isinstance(title, str) and title.strip() else text_of(anchor)
        resources.append(DocumentResource(title=(label or filename_from_url(url) or url).strip(), url=url, kind=kind))
    return tuple(resources)


def parse_listing(
    html: str,
    *,
    page_url: str,
    page_number: int,
    host: str,
    path_prefixes: tuple[str, ...],
) -> ListingPage | None:
    """### Parse one listing page, returning None when the report table is missing."""
    if not html.strip():
        return None
    soup = BeautifulSoup(html, "lxml")
    table = None
    indexes: tuple[int, int, int | None] = (0, 1, None)
    for candidate in soup.select("table"):
        headers = [text_of(cell).casefold() for cell in candidate.select("thead th")]
        if not headers:
            headers = [text_of(cell).casefold() for cell in candidate.select("tr th")]
        date_index = next((index for index, label in enumerate(headers) if "date" in label), None)
        title_index = next((index for index, label in enumerate(headers) if "title" in label), None)
        files_index = next((index for index, label in enumerate(headers) if "file" in label), None)
        if date_index is not None and title_index is not None:
            table = candidate
            indexes = (date_index, title_index, files_index)
            break
    if table is None:
        return None

    date_index, title_index, files_index = indexes
    entries: dict[str, ListingEntry] = {}
    for row in table.select("tbody tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        title_cell = _content(cells[title_index]) if title_index < len(cells) else None
        anchor = title_cell.select_one("a[href]") if title_cell is not None else None
        href = anchor.get("href") if isinstance(anchor, Tag) else None
        if not isinstance(href, str) or not href.strip():
            continue
        url = urljoin(page_url, href)
        if not is_report_url(url, host=host, path_prefixes=path_prefixes):
            continue
        title = text_of(anchor) if isinstance(anchor, Tag) else text_of(title_cell)
        if not title:
            continue
        file_cell = _content(cells[files_index]) if files_index is not None and files_index < len(cells) else None
        date_cell = _content(cells[date_index]) if date_index < len(cells) else None
        raw_date = text_of(date_cell)
        parsed_date = parse_listing_date(raw_date)
        identity = stable_identity(SOURCE_NAME, None, url)
        entries.setdefault(
            identity,
            ListingEntry(
                identity=identity,
                url=url,
                title=title,
                published_date_raw=parsed_date.raw,
                published_month=parsed_date.month,
                published_year=parsed_date.year,
                resources=_attachment_links(file_cell, page_url),
            ),
        )
    if not entries:
        return None
    start, end, total = _range_indicator(soup)
    return ListingPage(
        page_number=page_number,
        entries=tuple(entries.values()),
        total_reports=total,
        range_start=start,
        range_end=end,
    )


def parse_detail(html: str, *, url: str, min_body_words: int) -> RawArticle:
    """### Parse one report detail page, flagging heading-only content as incomplete."""
    if not html.strip():
        raise CommerceExtractionError(f"detail page is empty at {url}")
    soup = BeautifulSoup(html, "lxml")
    article = soup.select_one("article")
    if article is None:
        raise CommerceExtractionError(f"detail page has no article container at {url}")
    title = text_of(article.select_one("h1")) or text_of(soup.select_one("h1"))
    if not title:
        raise CommerceExtractionError(f"detail page has no report title at {url}")

    published_at: date | None = None
    raw_date: str | None = None
    time_node = article.select_one("time[datetime]") or soup.select_one("time[datetime]")
    if isinstance(time_node, Tag):
        raw_date = text_of(time_node) or None
        machine = time_node.get("datetime")
        if isinstance(machine, str) and machine.strip():
            try:
                published_at = datetime.fromisoformat(machine.strip().replace("Z", "+00:00")).date()
            except ValueError:
                published_at = None

    body = article.select_one(".field--name-body") or article
    paragraphs = [text for node in body.find_all(BODY_BLOCK_TAGS) if (text := text_of(node))]
    words = len(" ".join(paragraphs).split())

    raw = RawArticle(
        source=SOURCE_NAME,
        url=url,
        title=title,
        language=OUTPUT_LANGUAGE,
        published_at=published_at,
        published_date_raw=published_at.isoformat() if published_at is not None else raw_date,
        published_precision="day" if published_at is not None else None,
        paragraphs=paragraphs,
        resources=_detail_attachments(article, url),
    )
    if words < min_body_words:
        raise InsufficientReportContent(
            f"detail body has {words} word(s), below the {min_body_words}-word minimum",
            article=raw,
            words=words,
        )
    return raw


def _detail_attachments(article: Tag, url: str) -> tuple[DocumentResource, ...]:
    """### Collect the report page's own attachment links."""
    resources: list[DocumentResource] = []
    seen: set[str] = set()
    for anchor in article.select("a[href]"):
        href = anchor.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        target = urljoin(url, href)
        kind = resource_kind(target)
        if kind == "other" or target in seen:
            continue
        seen.add(target)
        label = text_of(anchor) or filename_from_url(target) or target
        resources.append(DocumentResource(title=label.strip(), url=target, kind=kind))
    return tuple(resources)


class CommerceReportsActor(Actor):
    """### Collect Commerce reports by acquiring each page through Firecrawl."""

    name = SOURCE_NAME

    def __init__(
        self,
        *,
        listing_url: str,
        firecrawl: FirecrawlScraper | None,
        max_pages: int = 6,
        max_detail_scrapes: int = 20,
        min_body_words: int = 30,
        source_name: str = SOURCE_NAME,
        is_known: Callable[[str], bool] | None = None,
        max_new_items: int | None = None,
    ) -> None:
        """### Configure the listing entry point, budgets, and the Firecrawl scraper."""
        self.listing_url = required_setting(listing_url, name="REPORTS_LISTING_URL")
        parsed = urlparse(self.listing_url)
        self.article_host = parsed.netloc.lower()
        self.article_paths = (f"{parsed.path.rstrip('/')}/",)
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
        """### Discover report listings through Firecrawl, then acquire each new report."""
        started_at = time.monotonic()
        result = ActorRunResult(source=self.source_name)
        if self.firecrawl is None:
            result.add_error("firecrawl is not configured; the Commerce source cannot be acquired")
            return result

        try:
            collected, complete = self._discover(result)
            result.articles_discovered = len(collected)
            self._acquire(collected, result)
        except FirecrawlError as error:
            # $ A provider-wide failure stops the run and is reported, never masked as empty.$
            self._logger.exception("commerce_firecrawl_failed", extra={"source": self.source_name})
            result.add_error(f"firecrawl failed: {error}")
            complete = False
        except Exception as error:
            self._logger.exception("commerce_actor_failed", extra={"source": self.source_name})
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

    def _discover(self, result: ActorRunResult) -> tuple[dict[str, ListingEntry], bool]:
        """### Page through the listing through Firecrawl until the overlap boundary or the end."""
        collected: dict[str, ListingEntry] = {}
        seen_pages: dict[str, int] = {}
        complete = False
        total_reports: int | None = None

        for page_number in range(self.max_pages):
            page = self._scrape_listing(page_number, result)
            if page is None:
                return collected, False
            if page.total_reports is not None:
                total_reports = page.total_reports
            # $ A report repeated on a different page means pagination is not being honoured.$
            repeated = {
                entry.identity
                for entry in page.entries
                if entry.identity in seen_pages and seen_pages[entry.identity] != page_number
            }
            if repeated:
                result.add_error(f"listing page {page_number} repeated {len(repeated)} report(s) from an earlier page")
                return collected, False
            for entry in page.entries:
                seen_pages.setdefault(entry.identity, page_number)

            new_on_page = 0
            for entry in page.entries:
                if entry.identity in collected:
                    continue
                collected[entry.identity] = entry
                new_on_page += 1
            if new_on_page == 0:
                complete = True
                break
            if total_reports is not None and page.range_end is not None and page.range_end >= total_reports:
                complete = True
                break
        else:
            result.add_error(f"stopped after hitting max_pages={self.max_pages}")

        return collected, complete

    def _scrape_listing(self, page_number: int, result: ActorRunResult) -> ListingPage | None:
        """### Scrape one listing page through Firecrawl and parse its report table."""
        page_url = build_page_url(self.listing_url, page_number)
        result.listing_pages_attempted += 1
        assert self.firecrawl is not None
        try:
            scraped = self.firecrawl.scrape(page_url)
        except FirecrawlError as error:
            result.listing_pages_failed += 1
            result.add_error(f"firecrawl listing failed for page {page_number}: {error}")
            return None
        result.provider_credits_used += scraped.credits_used
        if scraped.target_status is not None and scraped.target_status >= 400:
            result.listing_pages_failed += 1
            result.add_error(f"listing page {page_number} returned HTTP {scraped.target_status}")
            return None
        page = parse_listing(
            scraped.html,
            page_url=page_url,
            page_number=page_number,
            host=self.article_host,
            path_prefixes=self.article_paths,
        )
        if page is None:
            result.listing_pages_failed += 1
            result.add_error(f"listing page {page_number} did not contain the report table")
            return None
        result.listing_pages_succeeded += 1
        return page

    def _acquire(self, entries: dict[str, ListingEntry], result: ActorRunResult) -> None:
        """### Scrape a detail page for each new report, within the detail budget."""
        pending = [entry for entry in entries.values() if not self._is_known_entry(entry)]
        result.records_skipped_known += len(entries) - len(pending)
        if self._max_new_items is not None and len(pending) > self._max_new_items:
            result.anomaly_detected = True
            result.add_error(
                f"anomaly: {len(pending)} new report(s) exceeds the {self._max_new_items} item limit; crawl skipped"
            )
            return
        for index, entry in enumerate(pending):
            if index >= self.max_detail_scrapes:
                result.documents_pending = len(pending) - index
                result.add_error(
                    f"detail scrape budget reached; {result.documents_pending} report(s) pending for a later run"
                )
                return
            result.detail_pages_attempted += 1
            self._acquire_one(entry, result)

    def _is_known_entry(self, entry: ListingEntry) -> bool:
        """### Report whether a report identity was already ingested."""
        return self._is_known is not None and self._is_known(entry.identity)

    def _acquire_one(self, entry: ListingEntry, result: ActorRunResult) -> None:
        """### Acquire and validate one report, isolating its failures."""
        assert self.firecrawl is not None
        try:
            scraped = self.firecrawl.scrape(entry.url, cache=True)
        except FirecrawlError as error:
            self._fail_report(entry, result, f"firecrawl detail failed: {error}")
            return
        result.provider_credits_used += scraped.credits_used
        if scraped.target_status is not None and scraped.target_status >= 400:
            self._fail_report(entry, result, f"detail page returned HTTP {scraped.target_status}")
            return
        try:
            article = parse_detail(scraped.html, url=entry.url, min_body_words=self.min_body_words)
        except InsufficientReportContent as error:
            # $ The page parsed, so its metadata and attachments are still worth keeping.$
            self._store_article(error.article, entry, result, error=str(error))
            return
        except CommerceExtractionError as error:
            self._fail_report(entry, result, f"detail extraction failed: {error}")
            return
        self._store_article(article, entry, result)

    def _store_article(
        self,
        article: RawArticle,
        entry: ListingEntry,
        result: ActorRunResult,
        *,
        error: str | None = None,
    ) -> None:
        """### Canonicalize one report, keeping its discovery metadata and resources."""
        self._apply_listing_fallbacks(article, entry)
        article = article.model_copy(update={"resources": self._merge_resources(article.resources, entry.resources)})
        document = CanonicalDocument.from_raw(article)
        if error is not None:
            result.detail_pages_failed += 1
            result.records_without_text += 1
            result.records_missing_required_fields += 1
            result.add_error(f"{redact_url(entry.url)}: {error}")
            # $ An incomplete body is dropped so a failed record never carries partial text.$
            document = document.model_copy(
                update={"text": None, "content_hash": None, "extraction_status": "failed", "extraction_error": error}
            )
        else:
            result.detail_pages_succeeded += 1
        result.documents.append(document)

    def _fail_report(self, entry: ListingEntry, result: ActorRunResult, message: str) -> None:
        """### Record an unusable report while preserving its discovered metadata."""
        result.detail_pages_failed += 1
        result.records_missing_required_fields += 1
        result.records_without_text += 1
        result.add_error(f"{redact_url(entry.url)}: {message}")
        document = CanonicalDocument.from_listing_entry(
            ArticleStub(url=entry.url, title=entry.title),
            source=self.source_name,
            language=OUTPUT_LANGUAGE,
            extraction_status="failed",
            extraction_error=message,
            published_month=entry.published_month,
            published_year=entry.published_year,
            published_date_raw=entry.published_date_raw,
            published_precision="month" if entry.published_month and entry.published_year else None,
            resources=entry.resources,
        )
        result.documents.append(document)

    @staticmethod
    def _apply_listing_fallbacks(article: RawArticle, entry: ListingEntry) -> None:
        """### Fill a month-precision date from the listing when the page had none."""
        if article.published_at is not None or article.published_month is not None:
            return
        if entry.published_month is None or entry.published_year is None:
            return
        article.published_month = entry.published_month
        article.published_year = entry.published_year
        article.published_precision = "month"
        if article.published_date_raw is None:
            article.published_date_raw = entry.published_date_raw

    @staticmethod
    def _merge_resources(
        page_resources: tuple[DocumentResource, ...],
        listing_resources: tuple[DocumentResource, ...],
    ) -> tuple[DocumentResource, ...]:
        """### Keep every attachment known from the listing and the detail page."""
        merged: dict[str, DocumentResource] = {}
        for resource in (*page_resources, *listing_resources):
            key = resource.url or f"title:{resource.title}"
            merged.setdefault(key, resource)
        return tuple(merged.values())
