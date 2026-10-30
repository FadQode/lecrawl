"""### Tests for the source-agnostic validation rules."""

from __future__ import annotations

import pytest

from lexcrawl.models.document import TEXT_BEGIN_MARKER, compute_content_hash, ensure_text_marker
from lexcrawl.validation.base import (
    ValidationIssue,
    ValidationReport,
    host_issue,
    http_url_issue,
    text_issues,
)

HOST = "listing.example.test"


def test_report_separates_errors_from_warnings() -> None:
    """### Only errors make a report fail."""
    report = ValidationReport(
        issues=(
            ValidationIssue(field="a", code="x", message="bad"),
            ValidationIssue(field="b", code="y", message="meh", severity="warning"),
        )
    )
    assert report.ok is False
    assert len(report.errors) == 1
    assert len(report.warnings) == 1
    assert ValidationReport().ok is True


def test_reports_can_be_merged() -> None:
    """### Merging keeps every finding from both reports."""
    first = ValidationReport(issues=(ValidationIssue(field="a", code="x", message="m"),))
    second = ValidationReport(issues=(ValidationIssue(field="b", code="y", message="m"),))
    assert len(first.merged(second).issues) == 2


@pytest.mark.parametrize(
    ("value", "expected_code"),
    [
        (None, "url_missing"),
        ("   ", "url_missing"),
        ("not-a-url", "url_invalid"),
        ("ftp://example.test/x", "url_invalid"),
        ("/relative/path", "url_invalid"),
    ],
)
def test_http_url_issues_are_reported(value: str | None, expected_code: str) -> None:
    """### Missing and malformed URLs are reported with a code."""
    issue = http_url_issue(value, field="article_url")
    assert issue is not None
    assert issue.code == expected_code


def test_http_url_issue_accepts_valid_and_optional_values() -> None:
    """### Absolute URLs pass, and optional blanks are ignored."""
    assert http_url_issue("https://listing.example.test/ar/news/x", field="u") is None
    assert http_url_issue(None, field="u", required=False) is None
    assert http_url_issue("", field="u", required=False) is None


def test_host_scope_accepts_subdomains_only() -> None:
    """### A URL is in scope on the host or one of its subdomains."""
    assert host_issue(f"https://{HOST}/ar/news/x", field="u", expected_host=HOST) is None
    assert host_issue(f"https://sub.{HOST}/ar/news/x", field="u", expected_host=HOST) is None
    issue = host_issue("https://example.com/ar/news/x", field="u", expected_host=HOST)
    assert issue is not None and issue.code == "url_out_of_scope"


def test_completed_text_requires_marker_and_matching_hash() -> None:
    """### A complete localization's text and hash are cross-checked."""
    text = ensure_text_marker("collected body")
    assert text_issues(text, compute_content_hash(text), status="complete") == []

    marked = ensure_text_marker("collected body")
    missing_marker = text_issues("collected body", compute_content_hash(marked), status="complete")
    assert [issue.code for issue in missing_marker] == ["text_marker_missing"]

    bad_hash = text_issues(text, "0" * 64, status="complete")
    assert [issue.code for issue in bad_hash] == ["content_hash_mismatch"]

    no_hash = text_issues(text, None, status="complete")
    assert [issue.code for issue in no_hash] == ["content_hash_missing"]


def test_completed_text_must_not_be_empty() -> None:
    """### Completing without text is an error, never a silent success."""
    for value in (None, "", "   "):
        issues = text_issues(value, None, status="complete")
        assert [issue.code for issue in issues] == ["text_missing"]


@pytest.mark.parametrize("status", ["pending", "access_blocked", "failed", "unavailable"])
def test_uncollected_statuses_reject_text(status: str) -> None:
    """### A localization that did not collect text must not carry any."""
    text = ensure_text_marker("body")
    assert text_issues(None, None, status=status) == []
    assert [issue.code for issue in text_issues(text, compute_content_hash(text), status=status)] == [
        "text_unexpected",
        "content_hash_unexpected",
    ]
    assert [issue.code for issue in text_issues(None, "0" * 64, status=status)] == ["content_hash_unexpected"]


def test_text_issues_support_a_field_prefix() -> None:
    """### Findings can be attributed to a nested localization."""
    issues = text_issues(None, None, status="complete", field_prefix="translations.en.")
    assert issues[0].field == "translations.en.text"


def test_marker_requirement_matches_the_shared_constant() -> None:
    """### The rule uses the project marker rather than a local copy."""
    issues = text_issues("body only", compute_content_hash("body only"), status="complete")
    assert TEXT_BEGIN_MARKER in issues[0].message
