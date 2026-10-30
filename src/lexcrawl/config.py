"""### Centralized, validated configuration for LexCrawl."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """### Runtime settings resolved from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: str = Field(
        default="local",
        validation_alias=AliasChoices("LEXCRAWL_ENV", "environment"),
    )
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # $ persistence and API settings are declared here so every process resolves one contract.$
    database_url: str = "postgresql+psycopg://lexcrawl:lexcrawl@localhost:5432/lexcrawl"
    db_pool_size: int = Field(default=5, gt=0)
    db_max_overflow: int = Field(default=5, ge=0)

    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, gt=0, le=65535)
    api_page_size_default: int = Field(default=25, gt=0)
    api_page_size_max: int = Field(default=200, gt=0)

    # $ crawl policy shared by every source actor.$
    crawl_user_agent: str = "LexCrawl/0.1 (+https://github.com/OWNER/lexcrawl)"
    crawl_request_timeout_seconds: float = Field(default=30.0, gt=0)
    crawl_max_retries: int = Field(default=3, ge=0, le=10)
    crawl_retry_backoff_seconds: float = Field(default=1.5, ge=0)
    crawl_request_delay_seconds: float = Field(default=1.0, ge=0)
    crawl_concurrency_per_source: int = Field(default=3, ge=1, le=16)
    crawl_respect_robots_txt: bool = True
    crawl_max_listing_pages: int = Field(default=25, ge=1)
    crawl_engine: Literal["http", "browser"] = "http"

    # $ Source locations are supplied by the environment, so the repository names no site.$
    listings_url: str = ""
    listings_language: str = ""
    listings_secondary_url: str = ""
    crawl_bilingual: bool = False

    reports_api_url: str = ""
    reports_api_key: str = ""
    reports_article_host: str = ""
    reports_article_paths: str = ""
    reports_news_types: str = ""
    reports_page_size: int = Field(default=50, ge=1, le=200)
    reports_max_pages: int = Field(default=4, ge=1)

    releases_api_url: str = ""
    releases_site_url: str = ""
    releases_article_path: str = "/article/"
    releases_type_id: str = ""
    releases_api_language: str = ""
    releases_page_size: int = Field(default=50, ge=1, le=200)
    releases_max_pages: int = Field(default=10, ge=1)

    browser_headless: bool = True
    browser_wait_until: Literal["domcontentloaded", "load", "networkidle"] = "domcontentloaded"
    browser_settle_milliseconds: int = Field(default=250, ge=0)

    pdf_max_bytes: int = Field(default=26_214_400, gt=0)
    pdf_extraction_timeout_seconds: float = Field(default=60.0, gt=0)

    schedule_enabled: bool = True
    schedule_daily_cron: str = "0 3 * * *"
    schedule_timezone: str = "UTC"
    manual_trigger_token: str = ""

    # $ Extracted records are written here as timestamped JSON until the database layer exists.$
    output_dir: str = "data"


def required_setting(value: str, *, name: str) -> str:
    """### Return a required setting, failing fast with an actionable message."""
    if not value.strip():
        raise ValueError(f"{name} is not configured; set it in .env (see .env.example)")
    return value.strip()


def split_list(value: str) -> tuple[str, ...]:
    """### Split a comma-separated setting into its non-empty entries."""
    return tuple(part.strip() for part in value.split(",") if part.strip())


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """### Return the process-wide settings instance."""
    return Settings()
