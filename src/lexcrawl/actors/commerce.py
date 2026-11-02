"""### U.S. Department of Commerce reports Actor backed by the public news API."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from lexcrawl.actors.base import Actor, ActorRunResult
from lexcrawl.config import required_setting
from lexcrawl.crawling.base import AccessBlockedError, AccessDeniedError, Fetcher, FetchError, redact_url
from lexcrawl.logging import get_logger
from lexcrawl.models.document import CanonicalDocument
from lexcrawl.pipeline.pdf import is_pdf_content_type
from lexcrawl.validation.commerce import (
    SOURCE_NAME as CONTRACT_SOURCE_NAME,
)
from lexcrawl.validation.commerce import (
    CommerceReport,
    CommerceResource,
    display_titles,
    extract_pdf_resource,
    from_api_payload,
    is_report_url,
    to_canonical,
    validate_report,
)

LOGGER = get_logger(__name__)

SOURCE_NAME: str = CONTRACT_SOURCE_NAME


class CommerceApiError(ValueError):
    """### Raised when an API response cannot be used as a record page."""


@dataclass(frozen=True, slots=True)
class CommerceApiPage:
    """### Parsed API page: usable records plus the ones that were dropped."""

    records: tuple[dict[str, object], ...] = ()
    skipped_records: int = 0


def build_page_url(api_url: str, *, api_key: str, page_size: int, offset: int = 0) -> str:
    """### Build one API page URL with credentials and an explicit offset."""
    query = urlencode({"page[limit]": page_size, "page[offset]": offset})
    return with_api_key(f"{api_url}?{query}", api_key)


def with_api_key(url: str, api_key: str) -> str:
    """### Ensure an API URL carries the key, because page links omit it."""
    parts = urlparse(url)
    query = [(name, value) for name, value in parse_qsl(parts.query) if name != "api_key"]
    if api_key:
        query.append(("api_key", api_key))
    return urlunparse(parts._replace(query=urlencode(query)))


def news_type_labels(payload: dict[str, object]) -> tuple[str, ...]:
    """### Extract the news type labels from an API record."""
    entries = payload.get("news_type")
    if not isinstance(entries, list):
        return ()
    labels: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            label = entry.get("label")
            if isinstance(label, str) and label.strip():
                labels.append(label.strip())
    return tuple(labels)


def is_news_release(payload: dict[str, object], news_types: tuple[str, ...]) -> bool:
    """### Report whether a record carries one of the requested news types."""
    if not news_types:
        return True
    wanted = {label.lower() for label in news_types}
    return any(label.lower() in wanted for label in news_type_labels(payload))


def record_url(payload: dict[str, object]) -> str | None:
    """### Return the report page URL declared by an API record."""
    for key in ("href", "self"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_api_page(
    payload: bytes | str,
    *,
    page_url: str,
    host: str,
    path_prefixes: tuple[str, ...],
) -> CommerceApiPage:
    """### Parse one API response into raw records that pass identity checks."""
    try:
        document = json.loads(payload)
    except (TypeError, ValueError) as error:
        raise CommerceApiError(f"response from {page_url} is not JSON: {error}") from error
    if not isinstance(document, dict):
        raise CommerceApiError(f"response from {page_url} is not a JSON object")
    data = document.get("data")
    if not isinstance(data, list):
        raise CommerceApiError(f"response from {page_url} has no 'data' array")

    records: list[dict[str, object]] = []
    skipped = 0
    for entry in data:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        url = record_url(entry)
        title = entry.get("label") or entry.get("title")
        if not url or not isinstance(title, str) or not title.strip():
            skipped += 1
            continue
        if not is_report_url(url, host=host, path_prefixes=path_prefixes):
            skipped += 1
            continue
        records.append(entry)
    return CommerceApiPage(records=tuple(records), skipped_records=skipped)


@dataclass(slots=True)
class _RunState:
    """### Discovery bookkeeping for one Commerce run."""

    payloads: dict[str, dict[str, object]] = field(default_factory=dict)
    pagination_complete: bool = False


class CommerceReportsActor(Actor):
    """### Collect Commerce reports through the documented public API."""

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
        max_pdf_bytes: int = 26_214_400,
        pdf_timeout_seconds: float = 60.0,
        source_name: str = SOURCE_NAME,
    ) -> None:
        """### Configure the API endpoint, filter, and page bounds."""
        self.api_url = required_setting(api_url, name="REPORTS_API_URL")
        self.api_key = required_setting(api_key, name="REPORTS_API_KEY")
        self.article_host = required_setting(article_host, name="REPORTS_ARTICLE_HOST")
        self.article_paths = article_paths
        self.news_types = tuple(label.strip() for label in news_types if label.strip())
        self.max_pages = max(1, max_pages)
        self.page_size = max(1, page_size)
        self.language = language
        self.max_pdf_bytes = max_pdf_bytes
        self.pdf_timeout_seconds = pdf_timeout_seconds
        self.source_name = source_name
        self._logger = get_logger(f"{__name__}.{source_name}")

    def run(self, fetcher: Fetcher) -> ActorRunResult:
        """### Walk API pages, filter reports, and emit validated documents."""
        started_at = time.monotonic()
        result = ActorRunResult(source=self.source_name)
        state = _RunState()

        try:
            self._walk_api(fetcher, state, result)
            self._materialize(fetcher, state, result)
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

    def _walk_api(self, fetcher: Fetcher, state: _RunState, result: ActorRunResult) -> None:
        """### Walk the API by explicit offsets, never by following foreign-host links."""
        page_fingerprints: set[str] = set()
        for page_index in range(self.max_pages):
            page_url = build_page_url(
                self.api_url,
                api_key=self.api_key,
                page_size=self.page_size,
                offset=page_index * self.page_size,
            )
            result.listing_pages_attempted += 1
            try:
                page = parse_api_page(
                    fetcher.fetch(page_url).content,
                    page_url=redact_url(page_url),
                    host=self.article_host,
                    path_prefixes=self.article_paths,
                )
            except FetchError as error:
                result.listing_pages_failed += 1
                result.add_error(f"api fetch failed at {redact_url(page_url)}: {error}")
                return
            except CommerceApiError as error:
                result.listing_pages_failed += 1
                result.add_error(f"api parse failed: {error}")
                return

            result.listing_pages_succeeded += 1
            fingerprint = hashlib.sha256(
                json.dumps(page.records, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()
            if fingerprint in page_fingerprints:
                result.add_error(f"API repeated a page at offset {page_index * self.page_size}")
                return
            page_fingerprints.add(fingerprint)
            if page.skipped_records:
                result.add_error(f"skipped {page.skipped_records} unusable record(s) on {redact_url(page_url)}")
            for payload in page.records:
                url = record_url(payload)
                if url is None:
                    continue
                if not is_news_release(payload, self.news_types):
                    result.records_filtered_out += 1
                    continue
                if url in state.payloads:
                    result.article_urls_deduplicated += 1
                    continue
                state.payloads[url] = payload
            result.articles_discovered = len(state.payloads)

            if len(page.records) < self.page_size:
                state.pagination_complete = True
                return
        result.add_error(f"stopped after hitting max_pages={self.max_pages}")

    def _materialize(self, fetcher: Fetcher, state: _RunState, result: ActorRunResult) -> None:
        """### Validate each report and adapt it to the canonical contract."""
        if not state.payloads:
            result.add_error("no report records matched the API filter")
            return
        for url, payload in state.payloads.items():
            result.detail_pages_attempted += 1
            try:
                self._materialize_one(fetcher, url, payload, result)
            except Exception as error:
                self._logger.exception("report_materialization_failed", extra={"url": redact_url(url)})
                result.detail_pages_failed += 1
                result.records_missing_required_fields += 1
                result.add_error(f"report materialization failed at {redact_url(url)}: {error}")
                title = str(payload.get("label") or payload.get("title") or "Unavailable report").strip()
                result.documents.append(
                    CanonicalDocument(
                        source=CONTRACT_SOURCE_NAME,
                        source_url=url,
                        title=title or "Unavailable report",
                        language="en",
                        extraction_status="failed",
                        extraction_error=repr(error),
                        native_id=str(payload.get("id")) if payload.get("id") is not None else None,
                    )
                )

    def _materialize_one(self, fetcher: Fetcher, url: str, payload: dict[str, object], result: ActorRunResult) -> None:
        """### Isolate one API record's conversion and resource failures."""
        report = from_api_payload(payload)
        resources: list[CommerceResource] = []
        result.discover_resources(len(report.resources))
        for resource in report.resources:
            processed = self._process_resource(fetcher, resource, url)
            result.account_resource(processed.extraction_status)
            if processed.extraction_status in {"failed", "access_blocked", "url_unavailable"}:
                result.add_error(
                    f"resource {processed.extraction_status} at {redact_url(processed.original_url or url)}: "
                    f"{processed.error or processed.title}"
                )
            resources.append(processed)
        report = report.model_copy(update={"resources": display_titles(tuple(resources))})
        validation = validate_report(report, expected_host=self.article_host)
        for issue in validation.article.errors:
            result.add_error(f"{url} {issue.field} {issue.code}: {issue.message}")
        for issue in validation.article.warnings:
            self._logger.warning("report_warning", extra={"url": url, "code": issue.code})
        for index, report_issues in enumerate(validation.resources):
            for issue in report_issues.errors:
                result.add_error(f"{url} resources.{index} {issue.code}: {issue.message}")
            for issue in report_issues.warnings:
                self._logger.warning("resource_warning", extra={"url": url, "index": index, "code": issue.code})
        if not validation.ok:
            # $ A report that fails identity validation keeps only its metadata.$
            result.records_missing_required_fields += 1
            result.detail_pages_failed += 1
            result.documents.append(self._metadata_document(report))
            return
        document = to_canonical(report)
        if document.text is None:
            result.records_without_text += 1
            result.detail_pages_failed += 1
        else:
            result.detail_pages_succeeded += 1
        result.documents.append(document)

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
        self._logger.warning(
            "pdf_resource_failed",
            extra={"url": redact_url(resource.original_url), "parent_url": redact_url(report_url), "status": status},
        )
        return resource.model_copy(update={"extraction_status": status, "error": message})

    def _metadata_document(self, report: CommerceReport) -> CanonicalDocument:
        """### Store a report whose content failed validation without pretending it has text."""
        return CanonicalDocument(
            source=report.source,
            native_id=report.native_id,
            source_url=report.source_url,
            title=report.title,
            language=report.language,
            extraction_status="failed",
            extraction_error="report text is unavailable or failed validation",
            published_at=report.published_at,
            published_month=report.published_month,
            published_year=report.published_year,
            published_date_raw=report.published_date_raw,
            published_precision=report.published_precision,
            source_created_at=report.api_created_at,
            image_url=report.image_url,
            provenance=report.provenance,
        )
