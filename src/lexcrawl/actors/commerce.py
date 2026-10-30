"""### U.S. Department of Commerce reports Actor backed by the public news API."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from lexcrawl.actors.base import Actor, ActorRunResult
from lexcrawl.config import required_setting
from lexcrawl.crawling.base import Fetcher, FetchError
from lexcrawl.logging import get_logger
from lexcrawl.models.document import CanonicalDocument, RawArticle
from lexcrawl.pipeline.normalize import paragraphs_from_html

LOGGER = get_logger(__name__)

SOURCE_NAME = "commerce_reports"

_DATE_FORMATS = ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y")


class CommerceApiError(ValueError):
    """### Raised when an API response cannot be used as a record page."""


@dataclass(frozen=True, slots=True)
class CommerceRecord:
    """### One usable record from the Commerce news API."""

    native_id: str
    url: str
    title: str
    body_html: str | None
    summary: str | None
    published_at: date | None
    news_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CommerceApiPage:
    """### Parsed API page: usable records plus the next page link."""

    records: tuple[CommerceRecord, ...] = ()
    next_page_url: str | None = None
    skipped_records: int = 0
    date_unavailable: int = 0


def build_page_url(api_url: str, *, api_key: str, page_size: int) -> str:
    """### Build the first API page URL with credentials and page size."""
    return with_api_key(f"{api_url}?{urlencode({'page[limit]': page_size})}", api_key)


def with_api_key(url: str, api_key: str) -> str:
    """### Ensure an API URL carries the key, because page links omit it."""
    parts = urlparse(url)
    query = [(name, value) for name, value in parse_qsl(parts.query) if name != "api_key"]
    if api_key:
        query.append(("api_key", api_key))
    return urlunparse(parts._replace(query=urlencode(query)))


def is_report_url(url: str, *, host: str, path_prefixes: tuple[str, ...]) -> bool:
    """### Report whether a record URL is in scope for the configured source."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    candidate = parsed.netloc.lower()
    if candidate != host and not candidate.endswith(f".{host}"):
        return False
    return any(parsed.path.startswith(prefix) and len(parsed.path) > len(prefix) for prefix in path_prefixes)


def parse_timestamp(value: object) -> date | None:
    """### Parse a unix timestamp or date string, returning None when unusable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_unix(float(value))
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.isdigit():
        return _from_unix(float(text))
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    for date_format in _DATE_FORMATS:
        try:
            return datetime.strptime(text, date_format).date()
        except ValueError:
            continue
    return None


def _from_unix(value: float) -> date | None:
    """### Convert a unix timestamp into a date when it is representable."""
    try:
        return datetime.fromtimestamp(value, tz=UTC).date()
    except (OverflowError, OSError, ValueError):
        return None


def record_from_payload(
    payload: dict[str, object],
    *,
    host: str,
    path_prefixes: tuple[str, ...],
) -> CommerceRecord | None:
    """### Convert one API record into the contract, or None when unusable."""
    url = _first_text(payload.get("href"), payload.get("self"))
    title = _first_text(payload.get("label"), payload.get("title"))
    if url is None or title is None or not is_report_url(url, host=host, path_prefixes=path_prefixes):
        return None
    body = payload.get("body")
    return CommerceRecord(
        native_id=_first_text(payload.get("id"), payload.get("nid")) or "",
        url=url,
        title=title,
        body_html=body if isinstance(body, str) else None,
        summary=_first_text(payload.get("teaser"), payload.get("subtitle")),
        published_at=_publication_date(payload),
        news_types=_news_type_labels(payload.get("news_type")),
    )


def _publication_date(payload: dict[str, object]) -> date | None:
    """### Resolve the publication date from the fields the API provides."""
    for key in ("post_date", "post_date_formatted", "created", "updated"):
        parsed = parse_timestamp(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _news_type_labels(value: object) -> tuple[str, ...]:
    """### Extract news type labels from the API's news_type list."""
    if not isinstance(value, list):
        return ()
    labels = []
    for entry in value:
        if isinstance(entry, dict):
            label = _first_text(entry.get("label"))
            if label is not None:
                labels.append(label)
    return tuple(labels)


def _first_text(*values: object) -> str | None:
    """### Return the first non-blank textual value."""
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
    return None


def _next_page_url(document: dict[str, object]) -> str | None:
    """### Read the JSON:API next-page link when the API provides one."""
    links = document.get("links")
    if not isinstance(links, dict):
        return None
    next_link = links.get("next")
    if not isinstance(next_link, dict):
        return None
    return _first_text(next_link.get("href"))


def parse_api_page(
    payload: bytes | str,
    *,
    page_url: str,
    host: str,
    path_prefixes: tuple[str, ...],
) -> CommerceApiPage:
    """### Parse one API response into records and its pagination link."""
    try:
        document = json.loads(payload)
    except (TypeError, ValueError) as error:
        raise CommerceApiError(f"response from {page_url} is not JSON: {error}") from error
    if not isinstance(document, dict):
        raise CommerceApiError(f"response from {page_url} is not a JSON object")
    data = document.get("data")
    if not isinstance(data, list):
        raise CommerceApiError(f"response from {page_url} has no 'data' array")

    records: list[CommerceRecord] = []
    skipped = 0
    undated = 0
    for entry in data:
        record = record_from_payload(entry, host=host, path_prefixes=path_prefixes) if isinstance(entry, dict) else None
        if record is None:
            skipped += 1
            continue
        if record.published_at is None:
            undated += 1
        records.append(record)
    return CommerceApiPage(
        records=tuple(records),
        next_page_url=_next_page_url(document),
        skipped_records=skipped,
        date_unavailable=undated,
    )


@dataclass(slots=True)
class _PaginationState:
    """### Bookkeeping for one API pagination walk."""

    discovered: dict[str, CommerceRecord] = field(default_factory=dict)
    completed: bool = False


class CommerceReportsActor(Actor):
    """### Collect Commerce news records through the documented public API."""

    name = SOURCE_NAME
    requires_http_engine = True

    def __init__(
        self,
        *,
        api_url: str,
        api_key: str,
        article_host: str,
        article_paths: tuple[str, ...],
        news_types: tuple[str, ...],
        max_pages: int = 4,
        page_size: int = 50,
        language: str = "en",
        source_name: str = SOURCE_NAME,
    ) -> None:
        """### Configure the API endpoint, filter, and page bounds."""
        self.api_url = required_setting(api_url, name="REPORTS_API_URL")
        self.article_host = required_setting(article_host, name="REPORTS_ARTICLE_HOST")
        self.article_paths = article_paths
        self.api_key = required_setting(api_key, name="REPORTS_API_KEY")
        self.news_types = tuple(label.lower() for label in news_types)
        self.max_pages = max(1, max_pages)
        self.page_size = max(1, page_size)
        self.language = language
        self.source_name = source_name
        self._logger = get_logger(f"{__name__}.{source_name}")

    def run(self, fetcher: Fetcher) -> ActorRunResult:
        """### Walk API pages, filter records, and canonicalize documents."""
        started_at = time.monotonic()
        result = ActorRunResult(source=self.source_name)
        state = _PaginationState()

        self._walk_api(fetcher, state, result)
        self._materialize(state, result)

        result.pagination_complete = state.completed
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

    def _walk_api(self, fetcher: Fetcher, state: _PaginationState, result: ActorRunResult) -> None:
        """### Follow API pagination, returning when the pages run out."""
        visited: set[str] = set()
        page_url: str | None = build_page_url(self.api_url, api_key=self.api_key, page_size=self.page_size)
        while page_url is not None:
            if page_url in visited:
                result.add_error(f"pagination loop detected at {page_url}")
                return
            if result.listing_pages_attempted >= self.max_pages:
                result.add_error(f"stopped after hitting max_pages={self.max_pages}")
                return
            visited.add(page_url)
            result.listing_pages_attempted += 1

            try:
                page = parse_api_page(
                    fetcher.fetch(page_url).content,
                    page_url=page_url,
                    host=self.article_host,
                    path_prefixes=self.article_paths,
                )
            except FetchError as error:
                result.listing_pages_failed += 1
                result.add_error(f"api fetch failed at {page_url}: {error}")
                return
            except CommerceApiError as error:
                result.listing_pages_failed += 1
                result.add_error(f"api parse failed: {error}")
                return

            result.listing_pages_succeeded += 1
            if page.skipped_records:
                result.add_error(f"skipped {page.skipped_records} unusable record(s) on {page_url}")
            if page.date_unavailable:
                result.add_error(f"{page.date_unavailable} record(s) without a usable date on {page_url}")
            for record in page.records:
                if not self._matches_filter(record):
                    result.records_filtered_out += 1
                    continue
                if record.url in state.discovered:
                    result.article_urls_deduplicated += 1
                    continue
                state.discovered[record.url] = record
            result.articles_discovered = len(state.discovered)
            page_url = with_api_key(page.next_page_url, self.api_key) if page.next_page_url else None
        state.completed = True

    def _matches_filter(self, record: CommerceRecord) -> bool:
        """### Report whether a record carries one of the requested news types."""
        if not self.news_types:
            return True
        return any(label.lower() in self.news_types for label in record.news_types)

    def _materialize(self, state: _PaginationState, result: ActorRunResult) -> None:
        """### Convert collected records into canonical documents."""
        if not state.discovered:
            result.add_error("no report records matched the API filter")
            return
        for record in state.discovered.values():
            result.detail_pages_attempted += 1
            document = CanonicalDocument.from_raw(self._to_article(record))
            if document.text is None:
                # $ A record without any API text is still stored with its metadata.$
                result.detail_pages_failed += 1
                result.records_without_text += 1
                result.add_error(f"record has no extractable text: {record.url}")
            else:
                result.detail_pages_succeeded += 1
            result.documents.append(document)

    def _to_article(self, record: CommerceRecord) -> RawArticle:
        """### Build a raw article from one record, falling back to its summary."""
        paragraphs = paragraphs_from_html(record.body_html) or ([record.summary] if record.summary else [])
        return RawArticle(
            source=self.source_name,
            url=record.url,
            title=record.title,
            language=self.language,
            published_at=record.published_at,
            summary=record.summary,
            paragraphs=paragraphs,
        )
