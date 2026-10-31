"""### Security and retry utility tests for the shared fetcher contract."""

from datetime import UTC, datetime

import pytest

from lexcrawl.crawling.base import FetchError, redact_url, validate_http_url
from lexcrawl.crawling.retry import parse_retry_after, retry_delay


def test_redact_url_hides_api_credentials() -> None:
    """### API key and token query values are removed before diagnostics."""
    value = redact_url("https://api.example.test/data?api_key=secret&token=other&page=2")

    assert "secret" not in value
    assert "other" not in value
    assert "page=2" in value


@pytest.mark.parametrize(
    "url",
    ("file:///etc/passwd", "https://user:pass@example.test/", "http://127.0.0.1/", "http://localhost/admin"),
)
def test_validate_http_url_rejects_unsafe_destinations(url: str) -> None:
    """### Only public HTTP(S) destinations without embedded credentials are allowed."""
    with pytest.raises(FetchError):
        validate_http_url(url)


def test_validate_http_url_enforces_configured_hosts() -> None:
    """### Configured source hosts constrain redirect targets."""
    with pytest.raises(FetchError, match="host is not allowed"):
        validate_http_url("https://elsewhere.example.test/file", allowed_hosts=("cba.ca",))


def test_retry_after_accepts_seconds_and_http_dates() -> None:
    """### Retry-After supports both delay seconds and the standard HTTP date form."""
    now = datetime(2026, 10, 31, 0, 0, tzinfo=UTC)
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after("Sat, 31 Oct 2026 00:00:05 GMT", now=now) == 5.0
    assert parse_retry_after("invalid", now=now) is None


def test_retry_delay_uses_full_jitter_and_obeys_retry_after() -> None:
    """### Retry delay is randomized within its cap and never shorter than Retry-After."""
    assert retry_delay(2, backoff_seconds=1.0, random_value=0.5) == 1.0
    assert retry_delay(1, backoff_seconds=1.0, retry_after_seconds=4.0, random_value=0.0) == 4.0
