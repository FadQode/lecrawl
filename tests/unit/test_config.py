"""### Unit tests for settings and DSN resolution."""

from __future__ import annotations

from lexcrawl.config import Settings, required_setting
from lexcrawl.db.engine import database_configured, resolve_database_url


def test_defaults() -> None:
    """### Safe defaults are present."""
    settings = Settings(_env_file=None)
    assert settings.crawl_max_retries == 3
    assert settings.releases_page_size == 50


def test_required_setting() -> None:
    """### Blank required settings fail fast; values are trimmed."""
    assert required_setting("  https://x.test  ", name="X") == "https://x.test"
    try:
        required_setting("   ", name="X")
    except ValueError as error:
        assert "X is not configured" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_dsn_from_parts_escapes_special_chars() -> None:
    """### The DSN from parts safely encodes a password with @ and :."""
    settings = Settings(_env_file=None, database_url="", db_host="db", db_password="p@ss:word", db_name="lex")
    assert database_configured(settings)
    dsn = resolve_database_url(settings)
    assert "p%40ss%3Aword" in dsn or "p%40ss%3Aword" in dsn.replace("%3a", "%3A")


def test_database_url_preferred() -> None:
    """### An explicit DATABASE_URL wins over parts."""
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@h/db", db_host="ignored")
    assert resolve_database_url(settings) == "postgresql+psycopg://u:p@h/db"
