"""### U.S. Department of Commerce reports Actor: cheap preflight, Firecrawl acquisition."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from bs4.element import Tag

from lexcrawl.actors.base import Actor, ActorRunResult, CheckOutcome
from lexcrawl.config import required_setting
from lexcrawl.crawling.base import AccessBlockedError, AccessDeniedError, Fetcher, FetchError, redact_url
from lexcrawl.crawling.firecrawl import FirecrawlError, FirecrawlScraper
from lexcrawl.logging import get_logger
from lexcrawl.models.document import CanonicalDocument, compute_content_hash, ensure_text_marker, stable_identity
from lexcrawl.pipeline.normalize import text_of
from lexcrawl.pipeline.pdf import is_pdf_content_type
from lexcrawl.repositories.documents import IncompleteReport
from lexcrawl.validation.base import normalize_resource_type
from lexcrawl.validation.commerce import (
    SOURCE_NAME,
    CommerceReport,
    CommerceResource,
    display_titles,
    extract_pdf_resource,
    filename_from_url,
    is_report_url,
    parse_listing_date,
    to_canonical,
    validate_report,
)

LOGGER = get_logger(__name__)

RANGE_PATTERN = re.compile(r"Displaying\s+([\d,]+)\s*-\s*([\d,]+)\s+of\s+([\d,]+)")
ATTACHMENT_EXTENSIONS = frozenset({"pdf", "docx", "doc", "xlsx", "xls", "csv"})
BODY_BLOCK_TAGS = ("p", "li", "blockquote")


class CommerceExtractionError(ValueError):
    """### Raised when a Commerce page cannot be turned into a usable record."""


class InsufficientReportContent(CommerceExtractionError):
    """### Raised when a report page has no meaningful body content."""

    def __init__(self, message: str, *, report: CommerceReport, words: int) -> None:
        """### Keep the parsed metadata so the report can still be stored."""
        super().__init__(message)
        self.report = report
        self.words = words


@dataclass(frozen=True, slots=True)
class ListingEntry:
    """### One report row discovered on a Commerce reports listing page."""

    identity: str
    url: str
    title: str
    published_date_raw: str | None
    published_month: int | None
    published_year: int | None
    resources: tuple[CommerceResource, ...] = ()


@dataclass(frozen=True, slots=True)
class ListingPage:
    """### One parsed listing page with its source range indicator."""

    page_number: int
    entries: tuple[ListingEntry, ...]
    total_reports: int | None = None
    range_start: int | None = None
    range_end: int | None = None


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """### Outcome of the cheap change-detection check."""

    outcome: CheckOutcome
    known: frozenset[str]
    page: ListingPage | None = None
    reason: str | None = None


def build_page_url(listing_url: str, page_number: int) -> str:
    """### Build the validated explicit page URL for a listing page."""
    path = urlparse(listing_url).path
    return f"{listing_url}?q={path}&page={page_number}"


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


def _attachment_links(cell: Tag | None, page_url: str) -> tuple[CommerceResource, ...]:
    """### Collect attachment links from one File(s) cell."""
    if cell is None:
        return ()
    resources: list[CommerceResource] = []
    seen: set[str] = set()
    for anchor in cell.select("a[href]"):
        href = anchor.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        url = urljoin(page_url, href)
        extension = urlparse(url).path.rsplit(".", 1)[-1].lower() if "." in urlparse(url).path else ""
        if extension not in ATTACHMENT_EXTENSIONS or url in seen:
            continue
        seen.add(url)
        raw_title = anchor.get("title")
        title = raw_title if isinstance(raw_title, str) else None
        resources.append(
            CommerceResource(
                title=(title or text_of(anchor) or filename_from_url(url) or url).strip(),
                original_url=url,
                resource_type=normalize_resource_type(extension),
                extraction_status="pending",
            )
        )
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
        date_cell = _content(cells[date_index]) if date_index < len(cells) else None
        title_cell = _content(cells[title_index]) if title_index < len(cells) else None
        files_cell = _content(cells[files_index]) if files_index is not None and files_index < len(cells) else None
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
        raw_date = text_of(date_cell) or None
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
                resources=_attachment_links(files_cell, page_url),
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


def parse_detail(
    html: str,
    *,
    url: str,
    min_body_words: int,
) -> CommerceReport:
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
    body_text = "\n\n".join(paragraphs)
    words = len(body_text.split())

    resources = _detail_attachments(article, url)
    marked = ensure_text_marker(body_text) if words >= min_body_words and body_text.strip() else None
    report = CommerceReport(
        source=SOURCE_NAME,
        source_url=url,
        title=title,
        published_at=published_at,
        published_precision="day" if published_at is not None else None,
        published_date_raw=published_at.isoformat() if published_at is not None else raw_date,
        text=marked,
        content_hash=compute_content_hash(marked) if marked else None,
        resources=resources,
        provenance="live",
    )
    if words < min_body_words:
        raise InsufficientReportContent(
            f"detail body has {words} word(s), below the {min_body_words}-word minimum",
            report=report,
            words=words,
        )
    return report


def _detail_attachments(article: Tag, url: str) -> tuple[CommerceResource, ...]:
    """### Collect the report page's own attachment links."""
    resources: list[CommerceResource] = []
    seen: set[str] = set()
    for anchor in article.select("a[href]"):
        href = anchor.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        target = urljoin(url, href)
        extension = urlparse(target).path.rsplit(".", 1)[-1].lower() if "." in urlparse(target).path else ""
        if extension not in ATTACHMENT_EXTENSIONS or target in seen:
            continue
        seen.add(target)
        resources.append(
            CommerceResource(
                title=(filename_from_url(target) or text_of(anchor) or target).strip(),
                original_url=target,
                resource_type=normalize_resource_type(extension),
                extraction_status="pending",
            )
        )
    return tuple(resources)


class CommerceReportsActor(Actor):
    """### Crawl Commerce reports through a cheap preflight and Firecrawl acquisition."""

    name = SOURCE_NAME
    requires_http_engine = True

    def __init__(
        self,
        *,
        listing_url: str,
        firecrawl: FirecrawlScraper | None,
        known_identities: Callable[[], set[str]] | None = None,
        incomplete_reports: Callable[[], Sequence[IncompleteReport]] | None = None,
        bootstrap: bool = False,
        max_pages: int = 6,
        max_detail_scrapes: int = 20,
        max_incomplete_retries: int = 5,
        min_body_words: int = 30,
        max_pdf_bytes: int = 26_214_400,
        pdf_timeout_seconds: float = 60.0,
        source_name: str = SOURCE_NAME,
    ) -> None:
        """### Configure the listing entry point, budgets, and preflight access."""
        self.listing_url = required_setting(listing_url, name="REPORTS_LISTING_URL")
        parsed = urlparse(self.listing_url)
        self.article_host = parsed.netloc.lower()
        self.article_paths = (f"{parsed.path.rstrip('/')}/",)
        self.firecrawl = firecrawl
        self.known_identities = known_identities
        self.incomplete_reports = incomplete_reports
        self.bootstrap = bootstrap
        self.max_pages = max(1, max_pages)
        self.max_detail_scrapes = max(0, max_detail_scrapes)
        self.max_incomplete_retries = max(0, max_incomplete_retries)
        self.min_body_words = max(1, min_body_words)
        self.max_pdf_bytes = max_pdf_bytes
        self.pdf_timeout_seconds = pdf_timeout_seconds
        self.source_name = source_name
        self._logger = get_logger(f"{__name__}.{source_name}")

    def close(self) -> None:
        """### Release the Firecrawl client when it owns resources."""
        closer = getattr(self.firecrawl, "close", None)
        if callable(closer):
            closer()

    def run(self, fetcher: Fetcher) -> ActorRunResult:
        """### Check for new reports cheaply, then acquire only the new ones."""
        started_at = time.monotonic()
        result = ActorRunResult(source=self.source_name)
        if self.firecrawl is None:
            result.check_outcome = "check_failed"
            result.add_error("firecrawl is not configured")
            return result

        # $ Incomplete reports are retried independently, within a bounded, persisted budget.$
        retries = self._retry_entries(result)

        if self.bootstrap:
            # $ Bootstrap is explicitly authorised acquisition, never an implicit full crawl.$
            result.check_outcome = "new_data"
            known = self._known_identities(result)
            entries, complete = self._discover(None, known, result)
            LOGGER.info("commerce_bootstrap_started", extra={"known_identities": len(known)})
        else:
            preflight = self._preflight(fetcher, result)
            result.check_outcome = preflight.outcome
            if preflight.outcome == "check_failed":
                # $ A failed check never launches Firecrawl, not even to retry known reports.$
                result.documents_pending = len(retries)
                if retries:
                    result.add_error(f"{len(retries)} incomplete report(s) deferred until change detection succeeds")
                result.duration_seconds = time.monotonic() - started_at
                return result
            if preflight.outcome == "no_change":
                result.pagination_complete = True
                if not retries:
                    result.duration_seconds = time.monotonic() - started_at
                    return result
                entries, complete = dict(retries), True
                LOGGER.info("commerce_incomplete_retry", extra={"candidates": len(retries)})
            else:
                known = preflight.known
                entries, complete = self._discover(preflight.page, known, result)
                LOGGER.info("commerce_new_data_detected", extra={"known_identities": len(known)})

        # $ A freshly discovered report outranks a retry of the same identity.$
        for identity, entry in retries.items():
            entries.setdefault(identity, entry)
        self._acquire(fetcher, entries, result)
        result.pagination_complete = complete
        result.duration_seconds = time.monotonic() - started_at
        self._logger.info(
            "actor_run_finished",
            extra={
                "source": self.source_name,
                "status": result.status,
                "check_outcome": result.check_outcome,
                "documents": len(result.documents),
                "pending": result.documents_pending,
                "retries": len(retries),
                "credits": result.provider_credits_used,
                "pagination_complete": result.pagination_complete,
            },
        )
        return result

    def _retry_entries(self, result: ActorRunResult) -> dict[str, ListingEntry]:
        """### Load incomplete reports eligible for a bounded detail retry."""
        if self.incomplete_reports is None or self.max_incomplete_retries == 0:
            return {}
        try:
            candidates = list(self.incomplete_reports())[: self.max_incomplete_retries]
        except Exception as error:  # $ A failed retry lookup must not fail the run.$
            self._logger.exception("commerce_incomplete_lookup_failed")
            result.add_error(f"could not load incomplete reports: {type(error).__name__}")
            return {}
        return {
            candidate.identity: ListingEntry(
                identity=candidate.identity,
                url=candidate.url,
                title=candidate.title,
                published_date_raw=candidate.published_date_raw,
                published_month=candidate.published_month,
                published_year=candidate.published_year,
            )
            for candidate in candidates
        }

    def _known_identities(self, result: ActorRunResult) -> frozenset[str]:
        """### Load persisted identities, failing the run when they cannot be read."""
        if self.known_identities is None:
            result.add_error("change detection requires persisted documents")
            return frozenset()
        try:
            return frozenset(self.known_identities())
        except Exception as error:  # $ A failed baseline lookup is never treated as no-change.$
            self._logger.exception("commerce_known_identities_failed")
            result.add_error(f"could not load persisted identities: {type(error).__name__}")
            return frozenset()

    def _preflight(self, fetcher: Fetcher, result: ActorRunResult) -> PreflightResult:
        """### Detect new reports from the live listing using ordinary HTTP."""
        if self.known_identities is None:
            result.add_error("change detection requires the persistence layer")
            return PreflightResult("check_failed", frozenset(), reason="no identity source")
        try:
            known = frozenset(self.known_identities())
        except Exception as error:  # $ The baseline is mandatory for a trustworthy decision.$
            self._logger.exception("commerce_preflight_baseline_failed")
            result.add_error(f"change detection baseline failed: {type(error).__name__}")
            return PreflightResult("check_failed", frozenset(), reason="baseline failed")

        page_url = build_page_url(self.listing_url, 0)
        try:
            html = fetcher.fetch(page_url).content
        except FetchError as error:
            result.add_error(f"change detection fetch failed at {redact_url(page_url)}: {error}")
            return PreflightResult("check_failed", known, reason="listing fetch failed")
        page = parse_listing(
            html.decode("utf-8", "replace"),
            page_url=page_url,
            page_number=0,
            host=self.article_host,
            path_prefixes=self.article_paths,
        )
        if page is None:
            result.add_error("change detection found no report table on the listing page")
            return PreflightResult("check_failed", known, reason="listing table missing")
        new_entries = [entry for entry in page.entries if entry.identity not in known]
        if new_entries:
            return PreflightResult("new_data", known, page=page)
        return PreflightResult("no_change", known, page=page)

    def _discover(
        self,
        page_zero: ListingPage | None,
        known: frozenset[str],
        result: ActorRunResult,
    ) -> tuple[dict[str, ListingEntry], bool]:
        """### Paginate until the overlap boundary or the source end."""
        collected: dict[str, ListingEntry] = {}
        seen_pages: dict[str, int] = {}
        complete = False
        previous_end: int | None = None
        total_reports: int | None = page_zero.total_reports if page_zero else None

        for page_number in range(self.max_pages):
            page: ListingPage | None
            if page_number == 0 and page_zero is not None:
                page = page_zero
            else:
                page = self._scrape_listing(page_number, result)
                if page is None:
                    return self._finalize(collected, False, page_zero, known, result)
            if previous_end is not None and page.range_start is not None and page.range_start > previous_end + 1:
                result.add_error(f"listing range gap before page {page_number} ({previous_end} -> {page.range_start})")
                return self._finalize(collected, False, page_zero, known, result)
            if page.total_reports is not None:
                if total_reports is not None and page.total_reports != total_reports:
                    result.add_error(f"listing total changed between pages ({total_reports} -> {page.total_reports})")
                    return self._finalize(collected, False, page_zero, known, result)
                total_reports = page.total_reports
            span = (
                page.range_end - page.range_start + 1
                if page.range_start is not None and page.range_end is not None
                else None
            )
            if span is not None and span != len(page.entries):
                result.add_error(
                    f"listing range indicator on page {page_number} does not match its {len(page.entries)} row(s)"
                )
                return self._finalize(collected, False, page_zero, known, result)
            # $ A report repeated on a different page means pagination is not being honoured.$
            overlap = {
                entry.identity
                for entry in page.entries
                if entry.identity in seen_pages and seen_pages[entry.identity] != page_number
            }
            if overlap:
                result.add_error(
                    f"listing page {page_number} repeated {len(overlap)} report(s) already seen on an earlier page"
                )
                return self._finalize(collected, False, page_zero, known, result)
            for entry in page.entries:
                seen_pages.setdefault(entry.identity, page_number)
            previous_end = page.range_end

            new_on_page = 0
            for entry in page.entries:
                if entry.identity in collected or entry.identity in known:
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
            result.add_error(f"stopped after hitting firecrawl_max_pages_per_run={self.max_pages}")

        return self._finalize(collected, complete, page_zero, known, result)

    def _finalize(
        self,
        collected: dict[str, ListingEntry],
        complete: bool,
        page_zero: ListingPage | None,
        known: frozenset[str],
        result: ActorRunResult,
    ) -> tuple[dict[str, ListingEntry], bool]:
        """### Record discovery totals and flag any expected report left unconfirmed."""
        result.articles_discovered = len(collected)
        if page_zero is not None:
            expected = {entry.identity for entry in page_zero.entries if entry.identity not in known}
            missing = expected - set(collected)
            if missing:
                result.add_error(f"{len(missing)} expected new report(s) were not found while paginating")
                complete = False
        return collected, complete

    def _scrape_listing(self, page_number: int, result: ActorRunResult) -> ListingPage | None:
        """### Scrape one listing page through Firecrawl."""
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

    def _acquire(
        self,
        fetcher: Fetcher,
        entries: dict[str, ListingEntry],
        result: ActorRunResult,
    ) -> None:
        """### Scrape a detail page for each new report, within the detail budget."""
        pending_entries = list(entries.values())
        for index, entry in enumerate(pending_entries):
            if index >= self.max_detail_scrapes:
                result.documents_pending = len(pending_entries) - index
                result.add_error(
                    f"detail scrape budget reached; {result.documents_pending} report(s) pending for a later run"
                )
                break
            result.detail_pages_attempted += 1
            self._acquire_one(fetcher, entry, result)

    def _acquire_one(self, fetcher: Fetcher, entry: ListingEntry, result: ActorRunResult) -> None:
        """### Acquire and validate one report, isolating its failures."""
        assert self.firecrawl is not None
        try:
            scraped = self.firecrawl.scrape(entry.url, cache=True)
        except FirecrawlError as error:
            self._fail_report(fetcher, entry, result, f"firecrawl detail failed: {error}")
            return
        result.provider_credits_used += scraped.credits_used
        if scraped.target_status is not None and scraped.target_status >= 400:
            self._fail_report(fetcher, entry, result, f"detail page returned HTTP {scraped.target_status}")
            return
        try:
            report = parse_detail(scraped.html, url=entry.url, min_body_words=self.min_body_words)
        except InsufficientReportContent as error:
            # $ The page parsed, so its metadata and attachments are still worth keeping.$
            self._store_report(fetcher, error.report, entry, result, body_error=str(error))
            return
        except CommerceExtractionError as error:
            self._fail_report(fetcher, entry, result, f"detail extraction failed: {error}")
            return
        self._store_report(fetcher, report, entry, result)

    def _fail_report(self, fetcher: Fetcher, entry: ListingEntry, result: ActorRunResult, message: str) -> None:
        """### Record an unusable report while preserving its discovered metadata."""
        result.detail_pages_failed += 1
        result.records_missing_required_fields += 1
        result.add_error(f"{redact_url(entry.url)}: {message}")
        result.documents.append(self._metadata_document(fetcher, entry, error=message, result=result))

    def _store_report(
        self,
        fetcher: Fetcher,
        report: CommerceReport,
        entry: ListingEntry,
        result: ActorRunResult,
        *,
        body_error: str | None = None,
    ) -> None:
        """### Validate one parsed report, keeping its resources and outcome."""
        report = self._merge_resources(report, entry)
        report = report.model_copy(update={"resources": self._process_resources(fetcher, report, entry.url, result)})

        validation = validate_report(report, expected_host=self.article_host)
        for issue in validation.article.errors:
            result.add_error(f"{redact_url(entry.url)} {issue.field} {issue.code}: {issue.message}")
        for issue in validation.article.warnings:
            self._logger.warning("report_warning", extra={"url": redact_url(entry.url), "code": issue.code})
        for index, report_issues in enumerate(validation.resources):
            for issue in report_issues.errors:
                result.add_error(f"{redact_url(entry.url)} resources.{index} {issue.code}: {issue.message}")
            for issue in report_issues.warnings:
                self._logger.warning(
                    "resource_warning", extra={"url": redact_url(entry.url), "index": index, "code": issue.code}
                )

        document = to_canonical(report)
        if not validation.ok:
            result.detail_pages_failed += 1
            result.records_missing_required_fields += 1
            result.records_without_text += 1
            result.documents.append(
                document.model_copy(
                    update={"extraction_status": "failed", "extraction_error": "report validation failed"}
                )
            )
            return
        if document.text is None or body_error is not None:
            result.detail_pages_failed += 1
            result.records_without_text += 1
            result.records_missing_required_fields += 1
            reason = body_error or "report has no extractable body"
            result.add_error(f"{redact_url(entry.url)}: {reason}")
            result.documents.append(
                document.model_copy(update={"extraction_status": "failed", "extraction_error": reason})
            )
            return
        result.detail_pages_succeeded += 1
        result.documents.append(document)

    def _process_resources(
        self,
        fetcher: Fetcher,
        report: CommerceReport,
        report_url: str,
        result: ActorRunResult,
    ) -> tuple[CommerceResource, ...]:
        """### Run every attachment through the existing PDF pipeline."""
        result.discover_resources(len(report.resources))
        processed: list[CommerceResource] = []
        for resource in report.resources:
            outcome = self._process_resource(fetcher, resource, report_url)
            result.account_resource(outcome.extraction_status)
            if outcome.extraction_status in {"failed", "access_blocked", "url_unavailable"}:
                result.add_error(
                    f"resource {outcome.extraction_status} at {redact_url(outcome.original_url or report_url)}: "
                    f"{outcome.error or outcome.title}"
                )
            processed.append(outcome)
        return display_titles(tuple(processed))

    def _merge_resources(self, report: CommerceReport, entry: ListingEntry) -> CommerceReport:
        """### Keep every attachment known from the listing and the detail page."""
        merged: dict[str, CommerceResource] = {}
        for resource in (*entry.resources, *report.resources):
            key = resource.original_url or f"title:{resource.title}"
            merged.setdefault(key, resource)
        return report.model_copy(update={"resources": tuple(merged.values())})

    def _listing_report(self, entry: ListingEntry) -> CommerceReport:
        """### Build a month-precision report from listing metadata only."""
        return CommerceReport(
            source=SOURCE_NAME,
            source_url=entry.url,
            title=entry.title,
            published_month=entry.published_month,
            published_year=entry.published_year,
            published_date_raw=entry.published_date_raw,
            published_precision="month" if entry.published_month and entry.published_year else None,
            resources=entry.resources,
            provenance="live",
        )

    def _metadata_document(
        self,
        fetcher: Fetcher,
        entry: ListingEntry,
        *,
        error: str,
        result: ActorRunResult,
    ) -> CanonicalDocument:
        """### Store discovery metadata when the detail page could not be used."""
        report = self._merge_resources(self._listing_report(entry), entry)
        report = report.model_copy(update={"resources": self._process_resources(fetcher, report, entry.url, result)})
        document = to_canonical(report)
        return document.model_copy(update={"extraction_status": "failed", "extraction_error": error})

    def _process_resource(self, fetcher: Fetcher, resource: CommerceResource, report_url: str) -> CommerceResource:
        """### Download and extract one linked PDF while retaining its parent report."""
        if resource.original_url is None:
            return resource
        if resource.resource_type != "pdf":
            return resource.model_copy(update={"extraction_status": "not_applicable"})
        try:
            page = fetcher.fetch_binary(resource.original_url, max_bytes=self.max_pdf_bytes)
            if page.status_code is not None and not 200 <= page.status_code < 300:
                raise FetchError(
                    "PDF response returned an unsuccessful HTTP status",
                    url=resource.original_url,
                    status_code=page.status_code,
                )
            if not is_pdf_content_type(page.content_type):
                raise ValueError("response content type is not PDF")
            extracted = extract_pdf_resource(
                resource,
                page.content,
                max_bytes=self.max_pdf_bytes,
                timeout_seconds=self.pdf_timeout_seconds,
            )
            return extracted.model_copy(
                update={"mime_type": resource.mime_type or page.content_type or "application/pdf"}
            )
        except (AccessBlockedError, AccessDeniedError) as error:
            status = "access_blocked"
            message = str(error)
        except FetchError as error:
            status = "url_unavailable" if error.status_code in {404, 410} else "failed"
            message = str(error)
        except ValueError as error:
            status = "failed"
            message = str(error)
        except httpx.HTTPError as error:
            # $ A malformed attachment response is isolated to this resource, never the run.$
            status = "failed"
            message = f"attachment fetch failed: {error}"
        self._logger.warning(
            "pdf_resource_failed",
            extra={"url": redact_url(resource.original_url), "parent_url": redact_url(report_url), "status": status},
        )
        return resource.model_copy(update={"extraction_status": status, "error": message})
