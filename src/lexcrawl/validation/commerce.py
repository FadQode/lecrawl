"""### U.S. Department of Commerce report contract and validation rules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lexcrawl.models.document import (
    CanonicalDocument,
    DocumentResource,
    Provenance,
    PublicationPrecision,
    ResourceStatus,
    compute_content_hash,
    ensure_text_marker,
)
from lexcrawl.pipeline.pdf import PdfExtractionError, extract_pdf_text
from lexcrawl.validation.base import (
    RESOURCE_TYPES,
    ResourceType,
    ValidationIssue,
    ValidationReport,
    host_issue,
    http_url_issue,
    normalize_resource_type,
    text_issues,
)

SourceName = Literal["commerce_reports"]

SOURCE_NAME: SourceName = "commerce_reports"
OUTPUT_LANGUAGE = "en"
MONTH_PRECISION: PublicationPrecision = "month"
MIN_YEAR = 1800
MAX_YEAR = 2100
ARCHIVE_HOST = "web.archive.org"


class ListingDate(BaseModel):
    """### Parsed publication date from a Commerce listing month-year value."""

    model_config = ConfigDict(frozen=True)

    raw: str | None = None
    month: int | None = None
    year: int | None = None
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        """### Report whether the value parsed without a blocking finding."""
        return not any(issue.severity == "error" for issue in self.issues)


def parse_listing_date(value: object) -> ListingDate:
    """### Parse a listing month-year value without inventing a day."""
    raw = value.strip() if isinstance(value, str) else ""
    if not raw:
        return ListingDate(
            raw=raw or None,
            issues=(
                ValidationIssue(
                    field="published_date_raw",
                    code="date_missing",
                    message="publication date is not available from the listing",
                    severity="warning",
                ),
            ),
        )
    parsed: datetime | None = None
    for date_format in ("%B %Y", "%b %Y"):
        try:
            parsed = datetime.strptime(" ".join(raw.split()), date_format)
            break
        except ValueError:
            continue
    if parsed is None:
        return ListingDate(
            raw=raw,
            issues=(
                ValidationIssue(
                    field="published_date_raw",
                    code="date_unparseable",
                    message=f"publication date {raw!r} is not a month-year value",
                ),
            ),
        )
    if not MIN_YEAR <= parsed.year <= MAX_YEAR:
        return ListingDate(
            raw=raw,
            issues=(
                ValidationIssue(
                    field="published_year",
                    code="year_invalid",
                    message=f"publication year {parsed.year} is out of range",
                ),
            ),
        )
    return ListingDate(raw=" ".join(raw.split()), month=parsed.month, year=parsed.year)


class CommerceResource(BaseModel):
    """### One attachment belonging to a Commerce report."""

    model_config = ConfigDict(frozen=True)

    title: str
    original_url: str | None = None
    resource_type: ResourceType = "other"
    mime_type: str | None = None
    file_size: int | None = None
    extracted_text: str | None = None
    content_hash: str | None = None
    extraction_status: ResourceStatus = "pending"
    display_title: str | None = None
    error: str | None = None
    archive_url: str | None = None
    archived_at: datetime | None = None

    @field_validator("title")
    @classmethod
    def _require_title(cls, value: str) -> str:
        """### Reject a blank resource title."""
        if not value.strip():
            raise ValueError("resource title must not be blank")
        return value.strip()

    @field_validator("extracted_text")
    @classmethod
    def _mark_extracted_text(cls, value: str | None) -> str | None:
        """### Ensure extracted resource text carries the required marker."""
        if value is None or not value.strip():
            return None
        return ensure_text_marker(value)

    @model_validator(mode="after")
    def _check_extraction_state(self) -> CommerceResource:
        """### Keep the resource's text, hash, and status consistent."""
        if self.original_url is None and self.extraction_status != "url_unavailable":
            raise ValueError("a resource without a URL must be marked url_unavailable")
        if self.extraction_status == "complete":
            text = self.extracted_text
            if text is None or not text.strip():
                raise ValueError("complete resource extraction requires non-empty text")
            if not (self.content_hash or "").strip():
                raise ValueError("complete resource extraction requires a content hash")
            if self.content_hash != compute_content_hash(text):
                raise ValueError("resource content hash does not match the extracted text")
        elif self.extracted_text is not None:
            raise ValueError(f"status {self.extraction_status!r} must not carry extracted text")
        return self


class CommerceReport(BaseModel):
    """### One logical Commerce report with month-year publication precision."""

    model_config = ConfigDict(frozen=True)

    source: SourceName = SOURCE_NAME
    source_url: str
    title: str
    language: str = OUTPUT_LANGUAGE
    published_month: int | None = Field(default=None, ge=1, le=12)
    published_year: int | None = Field(default=None, ge=MIN_YEAR, le=MAX_YEAR)
    published_date_raw: str | None = None
    published_precision: PublicationPrecision | None = None
    text: str | None = None
    content_hash: str | None = None
    image_url: str | None = None
    native_id: str | None = None
    api_created_at: datetime | None = None
    resources: tuple[CommerceResource, ...] = ()
    provenance: Provenance = "live"
    archive_url: str | None = None
    archived_at: datetime | None = None
    archive_listing_url: str | None = None

    @field_validator("title")
    @classmethod
    def _require_title(cls, value: str) -> str:
        """### Reject a blank report title."""
        if not value.strip():
            raise ValueError("title must not be blank")
        return value.strip()

    @field_validator("source_url")
    @classmethod
    def _require_source_url(cls, value: str) -> str:
        """### Reject a blank report URL."""
        if not value.strip():
            raise ValueError("source_url must not be blank")
        return value.strip()

    @field_validator("text")
    @classmethod
    def _mark_article_text(cls, value: str | None) -> str | None:
        """### Ensure report text carries the required marker."""
        if value is None or not value.strip():
            return None
        return ensure_text_marker(value)

    @model_validator(mode="after")
    def _check_report_state(self) -> CommerceReport:
        """### Keep the report's precision, text, and hash consistent."""
        if self.published_precision == MONTH_PRECISION and (
            self.published_month is None or self.published_year is None
        ):
            raise ValueError("month precision requires both a month and a year")
        if self.text is None:
            if self.content_hash is not None:
                raise ValueError("a report without text must not carry a content hash")
            return self
        if not (self.content_hash or "").strip():
            raise ValueError("a report with text requires a content hash")
        if self.content_hash != compute_content_hash(self.text):
            raise ValueError("report content hash does not match the report text")
        return self

    @property
    def has_text(self) -> bool:
        """### Report whether the report body text was collected."""
        return self.text is not None

    @property
    def publication_period(self) -> str | None:
        """### Return the publication period as YYYY-MM without a day."""
        if self.published_year is None or self.published_month is None:
            return None
        return f"{self.published_year:04d}-{self.published_month:02d}"


class CommerceValidation(BaseModel):
    """### Article and per-resource validation for one Commerce report."""

    model_config = ConfigDict(frozen=True)

    article: ValidationReport
    resources: tuple[ValidationReport, ...] = ()

    @property
    def ok(self) -> bool:
        """### Report whether the report itself is valid."""
        return self.article.ok

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        """### Return every finding, report first then resources in order."""
        return (*self.article.issues, *(issue for report in self.resources for issue in report.issues))


def is_report_url(url: str, *, host: str, path_prefixes: tuple[str, ...]) -> bool:
    """### Report whether a record URL is in scope for the configured source."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    candidate = parsed.netloc.lower()
    if candidate != host and not candidate.endswith(f".{host}"):
        return False
    return any(parsed.path.startswith(prefix) and len(parsed.path) > len(prefix) for prefix in path_prefixes)


def filename_from_url(url: str | None) -> str | None:
    """### Return the file name of a resource URL when present."""
    if not url:
        return None
    from urllib.parse import unquote

    name = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    return name or None


def display_titles(resources: tuple[CommerceResource, ...]) -> tuple[CommerceResource, ...]:
    """### Give each of several PDFs a distinguishable display title."""
    pdf_indexes = [index for index, item in enumerate(resources) if item.resource_type == "pdf"]
    if len(pdf_indexes) < 2:
        return resources
    updated = list(resources)
    for position, index in enumerate(pdf_indexes, start=1):
        resource = resources[index]
        filename = filename_from_url(resource.original_url) or f"part {position}"
        updated[index] = resource.model_copy(update={"display_title": f"{resource.title} [{filename}]"})
    return tuple(updated)


def extract_pdf_resource(
    resource: CommerceResource,
    payload: bytes,
    *,
    max_bytes: int = 26_214_400,
    timeout_seconds: float = 60.0,
) -> CommerceResource:
    """### Extract a PDF payload into its resource, preserving the metadata."""
    if resource.resource_type != "pdf":
        raise ValueError("only PDF resources can be extracted")
    try:
        text = extract_pdf_text(payload, max_bytes=max_bytes, timeout_seconds=timeout_seconds)
    except PdfExtractionError as error:
        # $ A failed extraction keeps the attachment metadata and never stores partial text.$
        return resource.model_copy(update={"extraction_status": "failed", "error": str(error)})
    marked = ensure_text_marker(text)
    return CommerceResource(
        title=resource.title,
        original_url=resource.original_url,
        resource_type="pdf",
        mime_type="application/pdf",
        file_size=len(payload),
        extracted_text=marked,
        content_hash=compute_content_hash(marked),
        extraction_status="complete",
        display_title=resource.display_title,
        archive_url=resource.archive_url,
        archived_at=resource.archived_at,
    )


def validate_resource(
    resource: CommerceResource,
    *,
    expected_host: str,
    parent: CommerceReport | None = None,
) -> ValidationReport:
    """### Validate one attachment independently of its parent report."""
    issues: list[ValidationIssue] = []
    if parent is None:
        issues.append(
            ValidationIssue(
                field="parent",
                code="resource_without_parent",
                message="a resource must belong to a report",
            )
        )
    if not resource.title.strip():
        issues.append(ValidationIssue(field="title", code="title_missing", message="resource title is blank"))
    if resource.resource_type not in RESOURCE_TYPES:
        issues.append(
            ValidationIssue(
                field="resource_type", code="resource_type_unsupported", message="unsupported resource type"
            )
        )
    if resource.resource_type not in {"pdf", "excel"}:
        issues.append(
            ValidationIssue(
                field="resource_type",
                code="resource_type_unsupported",
                message=f"attachment type {resource.resource_type!r} is not extracted",
                severity="warning",
            )
        )

    if resource.original_url is None:
        if resource.extraction_status != "url_unavailable":
            issues.append(
                ValidationIssue(
                    field="original_url",
                    code="resource_url_missing",
                    message="a resource without a URL must be marked url_unavailable",
                )
            )
    else:
        problem = http_url_issue(resource.original_url, field="original_url")
        if problem is not None:
            issues.append(problem)
        else:
            problem = host_issue(resource.original_url, field="original_url", expected_host=expected_host)
            if problem is not None:
                issues.append(problem)

    issues.extend(text_issues(resource.extracted_text, resource.content_hash, status=resource.extraction_status))
    if resource.original_url is None or resource.extraction_status == "url_unavailable":
        issues.append(
            ValidationIssue(
                field="extraction_status",
                code="resource_url_unavailable",
                message="resource URL could not be recovered",
                severity="warning",
            )
        )
    if resource.extraction_status in {"failed", "access_blocked"}:
        issues.append(
            ValidationIssue(
                field="extraction_status",
                code="resource_extraction_failed",
                message=resource.error or "attachment extraction did not complete",
                severity="warning",
            )
        )
    if resource.resource_type == "pdf" and resource.extraction_status == "not_applicable":
        issues.append(
            ValidationIssue(
                field="extraction_status",
                code="resource_status_suspicious",
                message="a PDF resource should be processed rather than marked not_applicable",
                severity="warning",
            )
        )
    return ValidationReport(issues=tuple(issues))


def validate_report(report: CommerceReport, *, expected_host: str) -> CommerceValidation:
    """### Validate the report and every resource without cross-contaminating them."""
    issues: list[ValidationIssue] = []
    if report.source != SOURCE_NAME:
        issues.append(
            ValidationIssue(field="source", code="source_mismatch", message=f"source must be {SOURCE_NAME!r}")
        )
    problem = http_url_issue(report.source_url, field="source_url")
    if problem is not None:
        issues.append(problem)
    else:
        problem = host_issue(report.source_url, field="source_url", expected_host=expected_host)
        if problem is not None:
            issues.append(problem)
    if report.image_url:
        problem = http_url_issue(report.image_url, field="image_url", required=False)
        if problem is not None:
            issues.append(problem)

    if report.published_date_raw is not None:
        parsed = parse_listing_date(report.published_date_raw)
        issues.extend(parsed.issues)
        if parsed.ok and (parsed.month, parsed.year) != (report.published_month, report.published_year):
            issues.append(
                ValidationIssue(
                    field="published_date_raw",
                    code="date_mismatch",
                    message="stored month/year do not match the preserved listing date",
                )
            )
    elif report.published_month is None and report.published_year is None:
        issues.append(
            ValidationIssue(
                field="published_date_raw",
                code="date_unavailable",
                message="publication date is not verified for this report",
                severity="warning",
            )
        )
    if report.published_month is not None and report.published_year is not None:
        if report.published_precision is None:
            issues.append(
                ValidationIssue(
                    field="published_precision",
                    code="precision_missing",
                    message="month and year are set without a precision marker",
                    severity="warning",
                )
            )
    elif report.published_precision == MONTH_PRECISION:
        issues.append(
            ValidationIssue(
                field="published_precision",
                code="precision_incomplete",
                message="month precision requires a month and a year",
            )
        )

    issues.extend(
        text_issues(report.text, report.content_hash, status="complete" if report.text is not None else "pending")
    )

    resource_reports = [
        validate_resource(resource, expected_host=expected_host, parent=report) for resource in report.resources
    ]
    seen_urls: set[str] = set()
    for index, resource in enumerate(report.resources):
        if not resource.original_url:
            continue
        if resource.original_url in seen_urls:
            resource_reports[index] = resource_reports[index].merged(
                ValidationReport(
                    issues=(
                        ValidationIssue(
                            field=f"resources.{index}.original_url",
                            code="resource_duplicate",
                            message="the same resource URL is attached twice",
                        ),
                    )
                )
            )
        seen_urls.add(resource.original_url)

    return CommerceValidation(article=ValidationReport(issues=tuple(issues)), resources=tuple(resource_reports))


def to_canonical(report: CommerceReport) -> CanonicalDocument:
    """### Adapt a validated report to the canonical contract without inventing a day."""
    return CanonicalDocument(
        source=report.source,
        source_url=report.source_url,
        title=report.title,
        language=report.language,
        text=report.text,
        content_hash=report.content_hash,
        published_at=None,
        published_month=report.published_month,
        published_year=report.published_year,
        published_date_raw=report.published_date_raw,
        published_precision=report.published_precision,
        image_url=report.image_url,
        resources=tuple(
            DocumentResource(
                title=resource.display_title or resource.title,
                url=resource.original_url,
                kind=resource.resource_type,
                text=resource.extracted_text,
                error=resource.error,
                display_title=resource.display_title,
                mime_type=resource.mime_type,
                file_size=resource.file_size,
                content_hash=resource.content_hash,
                status=resource.extraction_status,
                archive_url=resource.archive_url,
                archived_at=resource.archived_at,
            )
            for resource in report.resources
        ),
        provenance=report.provenance,
        archive_url=report.archive_url,
        archived_at=report.archived_at,
        archive_listing_url=report.archive_listing_url,
    )


def archive_timestamp(value: str | None) -> datetime | None:
    """### Convert a Wayback capture timestamp into an aware datetime."""
    if not value:
        return None
    try:
        return datetime.strptime(value[:14], "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def archive_url_for(timestamp: str | None, original_url: str) -> str | None:
    """### Build the archived copy URL for an original URL."""
    if not timestamp:
        return None
    return f"https://{ARCHIVE_HOST}/web/{timestamp}/{original_url}"


def from_listing_row(
    row: dict[str, object],
    *,
    listing_capture: str,
    listing_url: str,
    site_root: str = "https://www.commerce.gov",
) -> CommerceReport:
    """### Build a report from one archived listing row, keeping capture provenance."""
    parsed = parse_listing_date(row.get("display_date"))
    attachments = tuple(str(url) for url in cast(list[str], row.get("pdf_urls") or []))
    resources = tuple(
        CommerceResource(
            title=filename_from_url(url) or f"Attachment {index}",
            original_url=url,
            resource_type=normalize_resource_type(str(url).split("?")[0].rsplit(".", 1)[-1].lower()),
            extraction_status="pending",
            archive_url=archive_url_for(listing_capture, url),
            archived_at=archive_timestamp(listing_capture),
        )
        for index, url in enumerate(attachments, start=1)
    )
    report_url = str(row.get("report_url"))
    return CommerceReport(
        source=SOURCE_NAME,
        source_url=report_url,
        title=str(row.get("title") or "").strip(),
        published_month=parsed.month,
        published_year=parsed.year,
        published_date_raw=parsed.raw,
        published_precision=MONTH_PRECISION if parsed.ok and parsed.month else None,
        resources=display_titles(resources),
        provenance="wayback",
        archive_url=archive_url_for(listing_capture, report_url),
        archived_at=archive_timestamp(listing_capture),
        archive_listing_url=archive_url_for(listing_capture, listing_url),
    )


def api_image_url(payload: dict[str, object]) -> str | None:
    """### Read the report image from the API's lowercase `image` field."""
    value = payload.get("image")
    if isinstance(value, str) and value.strip().startswith(("http://", "https://")):
        return value.strip()
    if not isinstance(value, dict):
        return None
    for key in ("ImageUrl", "url", "src", "path"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    elements = value.get("ImageElements")
    if isinstance(elements, list):
        for element in cast(list[object], elements):
            if isinstance(element, dict):
                candidate = element.get("ImageUrl")
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
    return None


def from_api_payload(
    payload: dict[str, object],
    *,
    image_url: str | None = None,
) -> CommerceReport:
    """### Build a report from one API record, preserving what the API actually states."""
    body = payload.get("body")
    text = ensure_text_marker(body) if isinstance(body, str) and body.strip() else None
    created = payload.get("created")
    created_at = None
    if (isinstance(created, (int, float)) and not isinstance(created, bool)) or (
        isinstance(created, str) and created.isdigit()
    ):
        created_at = datetime.fromtimestamp(float(created), tz=UTC)

    resources: list[CommerceResource] = []
    attachments_payload = cast(list[object], payload.get("documents") or [])
    for index, document in enumerate(attachments_payload, start=1):
        if not isinstance(document, dict):
            continue
        title = str(document.get("title") or "").strip() or f"Attachment {index}"
        mime = document.get("filemime") if isinstance(document.get("filemime"), str) else None
        size = document.get("filesize")
        file_size = int(size) if isinstance(size, str) and size.isdigit() else None
        kind = normalize_resource_type(title.rsplit(".", 1)[-1].lower()) if "." in title else "other"
        resources.append(
            CommerceResource(
                title=title,
                original_url=None,
                resource_type=kind,
                mime_type=mime,
                file_size=file_size,
                extraction_status="url_unavailable",
                error="attachment URL is not exposed by the API",
            )
        )

    return CommerceReport(
        source=SOURCE_NAME,
        source_url=str(payload.get("href") or payload.get("self") or "").strip(),
        title=str(payload.get("label") or payload.get("title") or "").strip(),
        text=text,
        content_hash=compute_content_hash(text) if text else None,
        image_url=image_url or api_image_url(payload),
        native_id=str(payload["id"]) if payload.get("id") is not None else None,
        api_created_at=created_at,
        resources=tuple(resources),
        provenance="live",
    )


__all__ = [
    "ARCHIVE_HOST",
    "MAX_YEAR",
    "MIN_YEAR",
    "MONTH_PRECISION",
    "OUTPUT_LANGUAGE",
    "SOURCE_NAME",
    "CommerceReport",
    "CommerceResource",
    "CommerceValidation",
    "ListingDate",
    "SourceName",
    "api_image_url",
    "archive_timestamp",
    "archive_url_for",
    "display_titles",
    "extract_pdf_resource",
    "filename_from_url",
    "from_api_payload",
    "from_listing_row",
    "is_report_url",
    "parse_listing_date",
    "to_canonical",
    "validate_report",
    "validate_resource",
]
