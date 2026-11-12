"""### Canadian Bar Association news releases Actor."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag

from lexcrawl.actors.base import Actor, ActorRunResult
from lexcrawl.config import required_setting
from lexcrawl.crawling.base import AccessBlockedError, AccessDeniedError, Fetcher, FetchError
from lexcrawl.logging import get_logger
from lexcrawl.models.document import (
    ArticleContentError,
    ArticleStub,
    CanonicalDocument,
    DocumentStatus,
    RawArticle,
    stable_identity,
)
from lexcrawl.pipeline.normalize import collapse_whitespace, paragraphs_of, text_of

LOGGER = get_logger(__name__)

SOURCE_NAME = "cba_news_releases"
API_NAME = "Article"
OUTPUT_LANGUAGE = "en"

TITLE_SELECTOR = "h1.printTitle"
TITLE_FALLBACK_SELECTOR = "h1"
TYPE_SELECTOR = "#MainContent_ctl00_ctl03_pnlArticleType .newBadge"
TYPE_FALLBACK_SELECTOR = ".newBadge"
DATE_SELECTOR = ".articleDateLogo"
TOPIC_SELECTOR = "section.tags a"
SUMMARY_SECTION_SELECTOR = "div.inline-summary"
BODY_SELECTOR = "#section-to-print"
# $ These blocks are rendered separately, so they must not repeat inside the body text.$
EXCLUDED_FROM_BODY = (SUMMARY_SECTION_SELECTOR, "section.tags")
_IMAGE_SELECTORS = ("div.articleHero img[src]", ".obscure-image img[src]", "picture img[src]")

_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S")


class CbaApiError(ValueError):
    """### Raised when a listing response cannot be used as article records."""


@dataclass(frozen=True, slots=True)
class CbaRecord:
    """### One article record returned by the CBA listing feed."""

    slug: str
    url: str
    title: str
    published_at: date | None
    image_url: str | None
    summary: str | None
    type_guids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CbaListingPage:
    """### Parsed listing response: usable records plus the dropped ones."""

    records: tuple[CbaRecord, ...] = ()
    skipped_records: int = 0


def article_url(slug: str, *, site_url: str, article_path: str) -> str:
    """### Build the canonical article URL for a slug."""
    return urljoin(f"{site_url.rstrip('/')}/", f"{article_path.strip('/')}/{slug}")


def article_scope(site_url: str) -> str:
    """### Derive the article host from the configured site URL."""
    return urlparse(site_url).netloc.lower()


def is_article_url(url: str, *, host: str, path_prefix: str) -> bool:
    """### Report whether a URL is an in-scope article of the configured source."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    candidate = parsed.netloc.lower()
    if candidate != host and not candidate.endswith(f".{host}"):
        return False
    return parsed.path.startswith(path_prefix) and len(parsed.path) > len(path_prefix)


def canonical_article_url(url: str) -> str:
    """### Normalize an article URL so the same article deduplicates to one key."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def parse_published_at(value: object) -> date | None:
    """### Parse an API or page date, returning None when unusable."""
    if not isinstance(value, str):
        return None
    cleaned = collapse_whitespace(value)
    if not cleaned:
        return None
    try:
        return date.fromisoformat(cleaned[:10])
    except ValueError:
        pass
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
    return None


def listing_request_body(*, language: str, page_size: int, skip: int) -> str:
    """### Build the listing request payload the site's own listing uses."""
    return json.dumps(
        {
            "name": "AdvContentRequest",
            "ApiName": API_NAME,
            "Language": language,
            "MaxRecords": page_size,
            "SkipRecords": skip,
            "Filter": [],
            "SortList": [],
            "Header": {},
            "CrossDomain": True,
            "Authenticate": False,
            "Action": "POST",
        }
    )


def record_from_payload(payload: dict[str, object], *, site_url: str, article_path: str) -> CbaRecord | None:
    """### Convert one listing record into a contract record, or None when unusable."""
    slug = _first_text(payload.get("SEOName"))
    title = _first_text(payload.get("Title"))
    if slug is None or title is None:
        return None
    url = canonical_article_url(article_url(slug, site_url=site_url, article_path=article_path))
    if not is_article_url(url, host=article_scope(site_url), path_prefix=article_path):
        return None
    published = parse_published_at(payload.get("ArticleDate")) or parse_published_at(payload.get("PostedDate"))
    return CbaRecord(
        slug=slug,
        url=url,
        title=title,
        published_at=published,
        image_url=_image_url(payload.get("Image"), site_url=site_url),
        summary=_first_text(payload.get("Summary")),
        type_guids=tuple(_string_list(payload.get("ArticleTypes"))),
    )


def parse_listing(payload: bytes | str, *, api_url: str, site_url: str, article_path: str) -> CbaListingPage:
    """### Parse one listing response into article records."""
    try:
        document = json.loads(payload)
    except (TypeError, ValueError) as error:
        raise CbaApiError(f"response from {api_url} is not JSON: {error}") from error
    if not isinstance(document, dict):
        raise CbaApiError(f"response from {api_url} is not a JSON object")
    data = document.get("Data")
    if not isinstance(data, list):
        raise CbaApiError(f"response from {api_url} has no 'Data' array")

    records: list[CbaRecord] = []
    skipped = 0
    for entry in data:
        record = (
            record_from_payload(entry, site_url=site_url, article_path=article_path)
            if isinstance(entry, dict)
            else None
        )
        if record is None:
            skipped += 1
            continue
        records.append(record)
    return CbaListingPage(records=tuple(records), skipped_records=skipped)


def is_news_release(record: CbaRecord, type_guid: str) -> bool:
    """### Report whether a record carries the news-release article type."""
    if not type_guid:
        return True
    return type_guid.lower() in {guid.lower() for guid in record.type_guids}


def parse_article(
    html: bytes | str,
    url: str,
    *,
    source: str = SOURCE_NAME,
    language: str = OUTPUT_LANGUAGE,
) -> RawArticle:
    """### Extract one news release from its article page."""
    soup = BeautifulSoup(html, "lxml")
    title = text_of(soup.select_one(TITLE_SELECTOR)) or text_of(soup.select_one(TITLE_FALLBACK_SELECTOR))
    if not title:
        raise ArticleContentError(f"article title not found at {url}")

    body = soup.select_one(BODY_SELECTOR)
    if body is None:
        raise ArticleContentError(f"article body {BODY_SELECTOR!r} not found at {url}")
    paragraphs = body_paragraphs(body)
    if not paragraphs:
        raise ArticleContentError(f"article body is empty at {url}")

    return RawArticle(
        source=source,
        url=canonical_article_url(url),
        title=title,
        language=language,
        published_at=parse_published_at(text_of(soup.select_one(DATE_SELECTOR))),
        summary=summary_points(soup),
        image_url=_image_url_from_page(soup, url),
        type_of_law=text_of(soup.select_one(TYPE_SELECTOR)) or text_of(soup.select_one(TYPE_FALLBACK_SELECTOR)) or None,
        topics=article_topics(soup),
        paragraphs=paragraphs,
    )


def body_paragraphs(body: Tag) -> list[str]:
    """### Read the article body in order, excluding separately rendered sections."""
    clone = BeautifulSoup(str(body), "lxml")
    for selector in EXCLUDED_FROM_BODY:
        for node in clone.select(selector):
            node.decompose()
    container = clone.select_one(BODY_SELECTOR) or clone
    return paragraphs_of(container)


def summary_points(soup: BeautifulSoup) -> str | None:
    """### Extract the Summary Points section as collected text."""
    section = soup.select_one(SUMMARY_SECTION_SELECTOR)
    if section is None:
        return None
    points = [text for text in (text_of(node) for node in section.select("p, li")) if text]
    if points:
        return "\n\n".join(points)
    return text_of(section) or None


def article_topics(soup: BeautifulSoup) -> tuple[str, ...]:
    """### Collect the topic tags exactly as the page displays them."""
    topics: list[str] = []
    for anchor in soup.select(TOPIC_SELECTOR):
        label = text_of(anchor)
        if label and label not in topics:
            topics.append(label)
    return tuple(topics)


def _image_url_from_page(soup: BeautifulSoup, url: str) -> str | None:
    """### Resolve an article image from the page when one is rendered."""
    for selector in _IMAGE_SELECTORS:
        node = soup.select_one(selector)
        if isinstance(node, Tag):
            source = node.get("src")
            if isinstance(source, str) and source.strip():
                return urljoin(url, source)
    return None


def _first_text(*values: object) -> str | None:
    """### Return the first non-blank textual value."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _string_list(value: object) -> list[str]:
    """### Collect the textual entries of a JSON list."""
    if not isinstance(value, list):
        return []
    return [entry.strip() for entry in value if isinstance(entry, str) and entry.strip()]


def _image_url(value: object, *, site_url: str) -> str | None:
    """### Resolve the card image URL from the listing image object."""
    if not isinstance(value, dict):
        return None
    elements = value.get("ImageElements")
    if not isinstance(elements, list):
        return None
    for element in elements:
        if isinstance(element, dict):
            url = _first_text(element.get("ImageUrl"))
            if url is not None:
                return url if url.startswith("http") else urljoin(f"{site_url.rstrip('/')}/", url.lstrip("/"))
    return None


@dataclass(slots=True)
class _RunState:
    """### Discovery bookkeeping for one CBA run."""

    discovered: dict[str, CbaRecord] = field(default_factory=dict)
    pagination_complete: bool = False


class CbaNewsReleasesActor(Actor):
    """### Collect CBA news releases through the listing feed and article pages."""

    name = SOURCE_NAME

    def __init__(
        self,
        *,
        api_url: str,
        site_url: str,
        type_guid: str,
        api_language: str,
        article_path: str = "/article/",
        type_label: str = "News Releases",
        page_size: int = 50,
        max_pages: int = 10,
        source_name: str = SOURCE_NAME,
        is_known: Callable[[str], bool] | None = None,
        max_new_items: int | None = None,
    ) -> None:
        """### Configure the listing feed and the enrichment bounds."""
        self.api_url = required_setting(api_url, name="RELEASES_API_URL")
        self.site_url = required_setting(site_url, name="RELEASES_SITE_URL")
        self.article_host = article_scope(self.site_url)
        self.article_path = article_path
        self.type_label = type_label
        self.api_language = api_language
        self.type_guid = type_guid
        self.page_size = max(1, page_size)
        self.max_pages = max(1, max_pages)
        self.source_name = source_name
        self._is_known = is_known
        self._max_new_items = max_new_items
        self._logger = get_logger(f"{__name__}.{source_name}")

    def run(self, fetcher: Fetcher) -> ActorRunResult:
        """### Discover news releases, then enrich each one from its page."""
        started_at = time.monotonic()
        result = ActorRunResult(source=self.source_name)
        state = _RunState()

        try:
            self._walk_listing(fetcher, state, result)
            self._enrich(fetcher, state, result)
        except Exception as error:
            self._logger.exception("actor_phase_failed", extra={"source": self.source_name})
            result.add_error(f"unexpected actor phase failure: {error!r}")

        result.pagination_complete = state.pagination_complete
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

    def _walk_listing(self, fetcher: Fetcher, state: _RunState, result: ActorRunResult) -> None:
        """### Page through the listing feed and keep news-release records."""
        for page_index in range(self.max_pages):
            result.listing_pages_attempted += 1
            try:
                page = self._fetch_listing_page(fetcher, skip=page_index * self.page_size)
            except FetchError as error:
                result.listing_pages_failed += 1
                result.add_error(f"listing fetch failed at {self.api_url}: {error}")
                return
            except CbaApiError as error:
                result.listing_pages_failed += 1
                result.add_error(f"listing parse failed: {error}")
                return

            result.listing_pages_succeeded += 1
            if page.skipped_records:
                result.add_error(f"skipped {page.skipped_records} unusable listing record(s)")
            for record in page.records:
                if not is_news_release(record, self.type_guid):
                    result.records_filtered_out += 1
                    continue
                if record.url in state.discovered:
                    result.article_urls_deduplicated += 1
                    continue
                state.discovered[record.url] = record
            result.articles_discovered = len(state.discovered)

            if len(page.records) < self.page_size:
                state.pagination_complete = True
                return
        result.add_error(f"stopped after hitting max_pages={self.max_pages}")

    def _fetch_listing_page(self, fetcher: Fetcher, *, skip: int) -> CbaListingPage:
        """### Request one listing page from the CBA listing feed."""
        payload = listing_request_body(language=self.api_language, page_size=self.page_size, skip=skip)
        response = fetcher.post_json(self.api_url, payload=payload)
        return parse_listing(
            response.content, api_url=self.api_url, site_url=self.site_url, article_path=self.article_path
        )

    def _enrich(self, fetcher: Fetcher, state: _RunState, result: ActorRunResult) -> None:
        """### Fetch each new article page, keeping metadata when enrichment fails."""
        if not state.discovered:
            result.add_error("no news releases matched the listing feed")
            return
        pending = [record for record in state.discovered.values() if not self._is_known_record(record)]
        result.records_skipped_known += len(state.discovered) - len(pending)
        if self._max_new_items is not None and len(pending) > self._max_new_items:
            result.anomaly_detected = True
            result.add_error(
                f"anomaly: {len(pending)} new release(s) exceeds the {self._max_new_items} item limit; crawl skipped"
            )
            return
        for record in pending:
            result.detail_pages_attempted += 1
            self._enrich_record(fetcher, record, result)

    def _is_known_record(self, record: CbaRecord) -> bool:
        """### Report whether a release identity was already ingested."""
        return self._is_known is not None and self._is_known(stable_identity(self.source_name, None, record.url))

    def _enrich_record(self, fetcher: Fetcher, record: CbaRecord, result: ActorRunResult) -> None:
        """### Fetch, parse and canonicalize one release without affecting siblings."""
        try:
            page = fetcher.fetch(record.url)
            article = parse_article(page.content, record.url)
        except FetchError as error:
            result.detail_pages_failed += 1
            result.add_error(f"article fetch failed at {record.url}: {error}")
            status: DocumentStatus = (
                "access_blocked"
                if isinstance(error, (AccessBlockedError, AccessDeniedError))
                else "unavailable"
                if error.status_code in {404, 410}
                else "failed"
            )
            result.documents.append(self._metadata_only(record, result, status=status, error=str(error)))
            return
        except ArticleContentError as error:
            result.detail_pages_failed += 1
            result.records_missing_required_fields += 1
            result.add_error(f"article parse failed at {record.url}: {error}")
            result.documents.append(self._metadata_only(record, result, error=str(error)))
            return

        self._apply_listing_fallbacks(article, record)
        try:
            document = CanonicalDocument.from_raw(article)
        except ValueError as error:
            # $ A release that fails the canonical contract keeps only its discovery metadata.$
            result.records_missing_required_fields += 1
            result.add_error(f"article validation failed at {record.url}: {error}")
            result.documents.append(self._metadata_only(record, result))
            return
        result.detail_pages_succeeded += 1
        result.documents.append(document)

    def _apply_listing_fallbacks(self, article: RawArticle, record: CbaRecord) -> None:
        """### Fill gaps in the article page from the listing record."""
        if article.published_at is None:
            article.published_at = record.published_at
        if not article.summary and record.summary:
            article.summary = record.summary
        if not article.image_url and record.image_url:
            article.image_url = record.image_url

    def _metadata_only(
        self,
        record: CbaRecord,
        result: ActorRunResult,
        *,
        status: DocumentStatus = "failed",
        error: str | None = None,
    ) -> CanonicalDocument:
        """### Store discovery metadata for an article that could not be enriched."""
        result.records_without_text += 1
        return CanonicalDocument.from_listing_entry(
            ArticleStub(
                url=record.url,
                title=record.title,
                published_at=record.published_at,
                summary=record.summary,
                image_url=record.image_url,
            ),
            source=self.source_name,
            language=OUTPUT_LANGUAGE,
            type_of_law=self.type_label,
            native_id=record.slug,
            extraction_status=status,
            extraction_error=error,
        )
