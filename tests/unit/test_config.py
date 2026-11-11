"""### Unit tests for validated settings."""

from __future__ import annotations

import pytest

from lexcrawl.config import Settings, required_setting


def test_settings_have_safe_defaults() -> None:
    """### Fields not present in the environment fall back to safe defaults."""
    settings = Settings(_env_file=None)
    assert settings.crawl_max_retries == 3
    assert settings.releases_page_size == 50


def test_required_setting_rejects_blank() -> None:
    """### A blank required setting fails fast with an actionable message."""
    with pytest.raises(ValueError, match="RELEASES_SITE_URL is not configured"):
        required_setting("   ", name="RELEASES_SITE_URL")


def test_required_setting_returns_trimmed_value() -> None:
    """### A configured value is returned without surrounding whitespace."""
    assert required_setting("  https://x.test  ", name="RELEASES_SITE_URL") == "https://x.test"
