"""### Source-agnostic validation rules shared by source validators."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from lexcrawl.models.document import TEXT_BEGIN_MARKER, compute_content_hash, ensure_text_marker

IssueSeverity = Literal["error", "warning"]
ResourceType = Literal["pdf", "excel", "word", "csv", "other"]
RESOURCE_TYPES: tuple[str, ...] = ("pdf", "excel", "word", "csv", "other")
_RESOURCE_TYPE_MAP: dict[str, ResourceType] = {
    "pdf": "pdf",
    "excel": "excel",
    "word": "word",
    "csv": "csv",
    "other": "other",
}


class ValidationIssue(BaseModel):
    """### One validation finding with its field, code, and severity."""

    model_config = ConfigDict(frozen=True)

    field: str
    code: str
    message: str
    severity: IssueSeverity = "error"


class ValidationReport(BaseModel):
    """### Collected validation findings for one article or localization."""

    model_config = ConfigDict(frozen=True)

    issues: tuple[ValidationIssue, ...] = Field(default_factory=tuple)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        """### Return only the blocking findings."""
        return tuple(issue for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        """### Return only the advisory findings."""
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def ok(self) -> bool:
        """### Report whether no blocking finding was collected."""
        return not self.errors

    def merged(self, other: ValidationReport) -> ValidationReport:
        """### Combine two reports into a new one."""
        return ValidationReport(issues=(*self.issues, *other.issues))


def normalize_resource_type(value: str) -> ResourceType:
    """### Normalize a detected file kind to a supported resource type."""
    return _RESOURCE_TYPE_MAP.get(value, "other")


def http_url_issue(value: str | None, *, field: str, required: bool = True) -> ValidationIssue | None:
    """### Check that a value is an absolute HTTP(S) URL."""
    if value is None or not value.strip():
        if not required:
            return None
        return ValidationIssue(field=field, code="url_missing", message=f"{field} is required")
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ValidationIssue(field=field, code="url_invalid", message=f"{field} is not an absolute HTTP(S) URL")
    return None


def host_issue(value: str, *, field: str, expected_host: str) -> ValidationIssue | None:
    """### Check that a URL belongs to the expected host."""
    host = urlparse(value).netloc.lower()
    expected = expected_host.lower().lstrip(".")
    if host == expected or host.endswith(f".{expected}"):
        return None
    return ValidationIssue(field=field, code="url_out_of_scope", message=f"{field} is outside {expected}")


def text_issues(
    text: str | None,
    content_hash: str | None,
    *,
    status: str,
    field_prefix: str = "",
) -> list[ValidationIssue]:
    """### Validate extracted text and its hash against the extraction status."""
    issues: list[ValidationIssue] = []
    text_field = f"{field_prefix}text" if field_prefix else "text"
    hash_field = f"{field_prefix}content_hash" if field_prefix else "content_hash"

    if status == "complete":
        if text is None or not text.strip():
            issues.append(
                ValidationIssue(field=text_field, code="text_missing", message="completed extraction requires text")
            )
        else:
            # $ Compare against the canonical form so the marker cannot hide a mismatch.$
            expected_hash = compute_content_hash(ensure_text_marker(text))
            if not text.startswith(TEXT_BEGIN_MARKER):
                issues.append(
                    ValidationIssue(
                        field=text_field,
                        code="text_marker_missing",
                        message=f"collected text must start with {TEXT_BEGIN_MARKER}",
                    )
                )
            if content_hash is None or not content_hash.strip():
                issues.append(
                    ValidationIssue(
                        field=hash_field, code="content_hash_missing", message="completed extraction requires a hash"
                    )
                )
            elif content_hash != expected_hash:
                issues.append(
                    ValidationIssue(
                        field=hash_field,
                        code="content_hash_mismatch",
                        message="content hash does not match the extracted text",
                    )
                )
        return issues

    if text is not None and text.strip():
        issues.append(
            ValidationIssue(
                field=text_field,
                code="text_unexpected",
                message=f"status {status!r} must not carry extracted text",
            )
        )
    if content_hash is not None and content_hash.strip():
        issues.append(
            ValidationIssue(
                field=hash_field,
                code="content_hash_unexpected",
                message=f"status {status!r} must not carry a content hash",
            )
        )
    return issues
