"""### Unit tests for credential redaction helpers."""

from __future__ import annotations

from lexcrawl.crawling.base import redact_message, redact_url


def test_redact_url_masks_sensitive_query_values() -> None:
    """### Credential-like query parameters are masked."""
    redacted = redact_url("https://x.test/a?api_key=secret123&page=2")
    assert "secret123" not in redacted
    assert "page=2" in redacted


def test_redact_message_masks_userinfo_and_keys() -> None:
    """### Userinfo and key assignments are masked in free text."""
    assert "hunter2" not in redact_message("failed at https://user:hunter2@x.test/a")
    assert "abc123" not in redact_message("boom api_key=abc123 end")


def test_redact_message_truncates() -> None:
    """### Long messages are truncated to the limit."""
    assert len(redact_message("x" * 5000, limit=100)) == 100
