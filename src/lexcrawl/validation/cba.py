"""### Canadian Bar Association news release contract and validation rules."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Literal
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lexcrawl.models.document import (
    CanonicalDocument,
    DocumentResource,
    ResourceStatus,
    compute_content_hash,
    ensure_text_marker,
)
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

SourceName = Literal["cba_news_releases"]
DocumentType = Literal["News Releases"]

SOURCE_NAME: SourceName = "cba_news_releases"
DOCUMENT_TYPE: DocumentType = "News Releases"
OUTPUT_LANGUAGE = "en"


def normalize_topics(values: object) -> tuple[str, ...]:
    """### Normalize topics to stripped, non-empty, de-duplicated strings."""
    if isinstance(values, str):
        candidates: Sequence[object] = (values,)
    elif isinstance(values, (list, tuple, set, frozenset)):
        candidates = list(values)
    else:
        return ()
    normalized: list[str] = []
    for value in candidates:
        text = str(value).strip()
        if text and text not in normalized:
            normalized.append(text)
    return tuple(normalized)


def filename_from_url(url: str | None) -> str | None:
    """### Return the decoded file name of a resource URL when present."""
    if not url:
        return None
    name = unquote(urlparse(url).path.rstrip("/").rsplit("/", 1)[-1])
    return name or None


class CbaResource(BaseModel):
    """### One additional resource attached to a CBA news release."""

    model_config = ConfigDict(frozen=True)

    title: str
    native_id: str | None = None
    original_url: str | None = None
    resource_type: ResourceType = "other"
    mime_type: str | None = None
    file_size: int | None = None
    page_count: int | None = Field(default=None, ge=0)
    character_count: int | None = Field(default=None, ge=0)
    extracted_text: str | None = None
    content_hash: str | None = None
    extraction_status: ResourceStatus = "pending"
    display_title: str | None = None
    error: str | None = None

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
    def _check_extraction_state(self) -> CbaResource:
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
            if self.character_count is not None and self.character_count != len(text):
                raise ValueError("resource character count does not match the extracted text")
        elif self.extracted_text is not None:
            raise ValueError(f"status {self.extraction_status!r} must not carry extracted text")
        return self


class CbaNewsRelease(BaseModel):
    """### One CBA news release with its article text and attached resources."""

    model_config = ConfigDict(frozen=True)

    source: SourceName = SOURCE_NAME
    source_url: str
    title: str
    document_type: DocumentType = DOCUMENT_TYPE
    native_id: str | None = None
    published_at: date | None = None
    topics: tuple[str, ...] = ()
    summary: str | None = None
    text: str | None = None
    content_hash: str | None = None
    image_url: str | None = None
    resources: tuple[CbaResource, ...] = ()

    @field_validator("title")
    @classmethod
    def _require_title(cls, value: str) -> str:
        """### Reject a blank news release title."""
        if not value.strip():
            raise ValueError("title must not be blank")
        return value.strip()

    @field_validator("source_url")
    @classmethod
    def _require_source_url(cls, value: str) -> str:
        """### Reject a blank news release URL."""
        if not value.strip():
            raise ValueError("source_url must not be blank")
        return value.strip()

    @field_validator("topics", mode="before")
    @classmethod
    def _normalize_topics(cls, value: object) -> tuple[str, ...]:
        """### Normalize the topic list while preserving page order."""
        return normalize_topics(value)

    @field_validator("text")
    @classmethod
    def _mark_article_text(cls, value: str | None) -> str | None:
        """### Ensure the article body carries the required marker."""
        if value is None or not value.strip():
            return None
        return ensure_text_marker(value)

    @model_validator(mode="after")
    def _check_article_state(self) -> CbaNewsRelease:
        """### Keep the article text, hash, and metadata consistent."""
        if self.text is None:
            if self.content_hash is not None:
                raise ValueError("an article without text must not carry a content hash")
            return self
        if not (self.content_hash or "").strip():
            raise ValueError("an article with text requires a content hash")
        if self.content_hash != compute_content_hash(self.text):
            raise ValueError("article content hash does not match the article text")
        return self

    @property
    def has_article_text(self) -> bool:
        """### Report whether the article body was collected."""
        return self.text is not None


class CbaReleaseValidation(BaseModel):
    """### Article and per-resource validation for one news release."""

    model_config = ConfigDict(frozen=True)

    article: ValidationReport
    resources: tuple[ValidationReport, ...] = ()

    @property
    def ok(self) -> bool:
        """### Report whether the news release itself is valid."""
        return self.article.ok

    def resource_report(self, index: int) -> ValidationReport:
        """### Return the validation report for one attached resource."""
        return self.resources[index]

    @property
    def issues(self) -> tuple[ValidationIssue, ...]:
        """### Return every finding, article first then resources in order."""
        return (*self.article.issues, *(issue for report in self.resources for issue in report.issues))


def display_titles(resources: tuple[CbaResource, ...]) -> tuple[CbaResource, ...]:
    """### Give each of several PDFs a distinguishable display title."""
    pdf_indexes = [index for index, item in enumerate(resources) if item.resource_type == "pdf"]
    if len(pdf_indexes) < 2:
        return resources
    updated = list(resources)
    for position, index in enumerate(pdf_indexes, start=1):
        resource = resources[index]
        suffix = filename_from_url(resource.original_url) or f"part {position}"
        updated[index] = resource.model_copy(update={"display_title": f"{resource.title} [{suffix}]"})
    return tuple(updated)


def validate_resource(resource: CbaResource, *, expected_host: str) -> ValidationReport:
    """### Validate one resource independently of its parent release."""
    issues: list[ValidationIssue] = []
    if not resource.title.strip():
        issues.append(ValidationIssue(field="title", code="title_missing", message="resource title is blank"))
    if resource.resource_type not in RESOURCE_TYPES:
        issues.append(
            ValidationIssue(
                field="resource_type", code="resource_type_unsupported", message="unsupported resource type"
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

    issues.extend(
        text_issues(
            resource.extracted_text,
            resource.content_hash,
            status=resource.extraction_status,
            field_prefix="",
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


def validate_release(release: CbaNewsRelease, *, expected_host: str) -> CbaReleaseValidation:
    """### Validate the release and every resource without cross-contaminating them."""
    issues: list[ValidationIssue] = []
    if release.source != SOURCE_NAME:
        issues.append(
            ValidationIssue(field="source", code="source_mismatch", message=f"source must be {SOURCE_NAME!r}")
        )
    if release.document_type != DOCUMENT_TYPE:
        issues.append(
            ValidationIssue(
                field="document_type",
                code="document_type_mismatch",
                message=f"document_type must be {DOCUMENT_TYPE!r}",
            )
        )
    problem = http_url_issue(release.source_url, field="source_url")
    if problem is not None:
        issues.append(problem)
    else:
        problem = host_issue(release.source_url, field="source_url", expected_host=expected_host)
        if problem is not None:
            issues.append(problem)
    if release.image_url:
        problem = http_url_issue(release.image_url, field="image_url", required=False)
        if problem is not None:
            issues.append(problem)

    for index, topic in enumerate(release.topics):
        if not topic.strip():
            issues.append(
                ValidationIssue(field=f"topics.{index}", code="topic_missing", message="topic must not be blank")
            )

    issues.extend(
        text_issues(
            release.text,
            release.content_hash,
            status="complete" if release.text is not None else "pending",
        )
    )

    resource_reports = [validate_resource(resource, expected_host=expected_host) for resource in release.resources]
    seen_urls: set[str] = set()
    seen_names: set[tuple[str, str]] = set()
    for index, resource in enumerate(release.resources):
        duplicates: list[ValidationIssue] = []
        if resource.original_url is not None:
            if resource.original_url in seen_urls:
                duplicates.append(
                    ValidationIssue(
                        field=f"resources.{index}.original_url",
                        code="resource_duplicate",
                        message="the same resource URL is attached twice",
                    )
                )
            seen_urls.add(resource.original_url)
        else:
            key = (resource.title, resource.resource_type)
            if key in seen_names:
                duplicates.append(
                    ValidationIssue(
                        field=f"resources.{index}.title",
                        code="resource_duplicate_title",
                        message="a resource with the same title and type is attached twice",
                        severity="warning",
                    )
                )
            seen_names.add(key)
        if duplicates:
            resource_reports[index] = resource_reports[index].merged(ValidationReport(issues=tuple(duplicates)))

    return CbaReleaseValidation(article=ValidationReport(issues=tuple(issues)), resources=tuple(resource_reports))


def to_canonical(release: CbaNewsRelease) -> CanonicalDocument:
    """### Adapt a validated news release to the canonical contract."""
    return CanonicalDocument(
        source=release.source,
        native_id=release.native_id,
        source_url=release.source_url,
        title=release.title,
        language=OUTPUT_LANGUAGE,
        extraction_status="complete" if release.text is not None else "failed",
        text=release.text,
        content_hash=release.content_hash,
        published_at=release.published_at,
        summary=release.summary,
        image_url=release.image_url,
        type_of_law=release.document_type,
        topics=release.topics,
        resources=tuple(
            DocumentResource(
                title=resource.display_title or resource.title,
                native_id=resource.native_id,
                parent_identity=f"{release.source}:id:{release.native_id}" if release.native_id else None,
                url=resource.original_url,
                kind=resource.resource_type,
                text=resource.extracted_text,
                error=resource.error,
                display_title=resource.display_title,
                mime_type=resource.mime_type,
                file_size=resource.file_size,
                page_count=resource.page_count,
                character_count=resource.character_count,
                content_hash=resource.content_hash,
                status=resource.extraction_status,
            )
            for resource in release.resources
        ),
    )


__all__ = [
    "DOCUMENT_TYPE",
    "OUTPUT_LANGUAGE",
    "RESOURCE_TYPES",
    "SOURCE_NAME",
    "CbaNewsRelease",
    "CbaReleaseValidation",
    "CbaResource",
    "DocumentType",
    "ResourceType",
    "SourceName",
    "display_titles",
    "filename_from_url",
    "normalize_resource_type",
    "normalize_topics",
    "to_canonical",
    "validate_release",
    "validate_resource",
]
