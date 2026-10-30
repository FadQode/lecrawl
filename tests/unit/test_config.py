"""### Configuration tests for validated environment settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lexcrawl.config import Settings, get_settings, required_setting, split_list

REQUIRED_ENVIRONMENT_VARIABLES = (
    "LEXCRAWL_ENV",
    "LOG_LEVEL",
    "LOG_FORMAT",
    "DATABASE_URL",
    "CRAWL_USER_AGENT",
    "CRAWL_REQUEST_TIMEOUT_SECONDS",
    "CRAWL_MAX_RETRIES",
    "CRAWL_REQUEST_DELAY_SECONDS",
    "CRAWL_CONCURRENCY_PER_SOURCE",
    "CRAWL_RESPECT_ROBOTS_TXT",
    "CRAWL_MAX_LISTING_PAGES",
    "LISTINGS_URL",
    "LISTINGS_LANGUAGE",
    "REPORTS_API_URL",
    "REPORTS_API_KEY",
    "REPORTS_ARTICLE_HOST",
    "REPORTS_ARTICLE_PATHS",
    "REPORTS_NEWS_TYPES",
    "REPORTS_MAX_PAGES",
    "REPORTS_PAGE_SIZE",
    "RELEASES_API_URL",
    "RELEASES_SITE_URL",
    "RELEASES_ARTICLE_PATH",
    "RELEASES_TYPE_ID",
    "RELEASES_API_LANGUAGE",
    "RELEASES_MAX_PAGES",
    "RELEASES_PAGE_SIZE",
    "OUTPUT_DIR",
)


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """### Remove declared variables so defaults are deterministic."""
    for name in REQUIRED_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_defaults_keep_crawl_policy_and_leave_sources_unset() -> None:
    """### Crawl policy has safe defaults and no source location is baked in."""
    settings = Settings(_env_file=None)
    assert settings.environment == "local"
    assert settings.log_format == "json"
    assert settings.crawl_request_timeout_seconds == 30.0
    assert settings.crawl_respect_robots_txt is True
    assert settings.crawl_max_listing_pages == 25
    assert settings.listings_url == ""
    assert settings.reports_api_url == ""
    assert settings.releases_api_url == ""


def test_required_setting_fails_fast_with_an_actionable_message() -> None:
    """### A missing source location names the variable to set."""
    assert required_setting("  https://example.test  ", name="LISTINGS_URL") == "https://example.test"
    with pytest.raises(ValueError, match="LISTINGS_URL is not configured"):
        required_setting("", name="LISTINGS_URL")
    with pytest.raises(ValueError, match="RELEASES_TYPE_ID is not configured"):
        required_setting("   ", name="RELEASES_TYPE_ID")


def test_split_list_parses_comma_separated_settings() -> None:
    """### Comma-separated settings become ordered tuples."""
    assert split_list("/news/,/reports/") == ("/news/", "/reports/")
    assert split_list(" Reports , Fact sheets , ") == ("Reports", "Fact sheets")
    assert split_list("") == ()


def test_environment_variables_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """### Declared environment variables take precedence over defaults."""
    monkeypatch.setenv("LEXCRAWL_ENV", "staging")
    monkeypatch.setenv("LISTINGS_URL", "https://example.test/ar/news")
    monkeypatch.setenv("CRAWL_MAX_RETRIES", "1")
    monkeypatch.setenv("CRAWL_RESPECT_ROBOTS_TXT", "false")
    settings = Settings(_env_file=None)
    assert settings.environment == "staging"
    assert settings.listings_url == "https://example.test/ar/news"
    assert settings.crawl_max_retries == 1
    assert settings.crawl_respect_robots_txt is False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("LOG_FORMAT", "verbose"),
        ("CRAWL_REQUEST_TIMEOUT_SECONDS", "0"),
        ("CRAWL_CONCURRENCY_PER_SOURCE", "0"),
        ("CRAWL_MAX_RETRIES", "99"),
        ("API_PORT", "70000"),
    ],
)
def test_invalid_values_are_rejected(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    """### Invalid configuration fails fast instead of at crawl time."""
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_get_settings_is_cached() -> None:
    """### The settings accessor resolves one instance per process."""
    get_settings.cache_clear()
    assert get_settings() is get_settings()
    get_settings.cache_clear()
