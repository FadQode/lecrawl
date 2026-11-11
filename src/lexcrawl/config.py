"""### Centralized, validated configuration for LexCrawl."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """### Runtime settings resolved from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: str = "local"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # $ Crawl policy shared by every source actor.$
    crawl_user_agent: str = "LexCrawl/0.1 (+https://github.com/OWNER/lexcrawl)"
    crawl_request_timeout_seconds: float = Field(default=30.0, gt=0)
    crawl_max_retries: int = Field(default=3, ge=0, le=10)
    crawl_retry_backoff_seconds: float = Field(default=1.5, ge=0)
    crawl_request_delay_seconds: float = Field(default=1.0, ge=0)
    crawl_max_listing_pages: int = Field(default=25, ge=1)
    crawl_max_response_bytes: int = Field(default=26_214_400, gt=0)

    # $ Source locations come from the environment, so the repository names no live site.$
    releases_api_url: str = ""
    releases_site_url: str = ""
    releases_article_path: str = "/article/"
    releases_type_id: str = ""
    releases_api_language: str = "en-us"
    releases_page_size: int = Field(default=50, ge=1, le=200)
    releases_max_pages: int = Field(default=10, ge=1)

    # $ Extracted records are written here as timestamped JSON.$
    output_dir: str = "data"


def required_setting(value: str, *, name: str) -> str:
    """### Return a required setting, failing fast with an actionable message."""
    if not value.strip():
        raise ValueError(f"{name} is not configured; set it in .env (see .env.example)")
    return value.strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """### Return the process-wide settings instance."""
    return Settings()
