"""### Centralized, validated configuration for LexCrawl."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """### Runtime settings resolved from environment variables and `.env`."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Runtime ------------------------------------------------------------
    environment: str = "local"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # --- Crawl policy (shared by every source actor) ------------------------
    crawl_user_agent: str = "LexCrawl/0.1 (+https://github.com/OWNER/lexcrawl)"
    crawl_request_timeout_seconds: float = Field(default=30.0, gt=0)
    crawl_max_retries: int = Field(default=3, ge=0, le=10)
    crawl_retry_backoff_seconds: float = Field(default=1.5, ge=0)
    crawl_request_delay_seconds: float = Field(default=1.0, ge=0)
    crawl_max_listing_pages: int = Field(default=25, ge=1)
    crawl_max_response_bytes: int = Field(default=26_214_400, gt=0)
    crawl_cache_max_entries: int = Field(default=256, ge=0)

    # --- UAE Legislation (news) --------------------------------------------
    listings_url: str = ""
    listings_language: str = "ar"
    uae_max_detail_scrapes_per_run: int = Field(default=20, ge=0, le=500)
    # $ UAE is fetched directly when it answers without a challenge, with Firecrawl as the fallback.$
    uae_direct_fetch: bool = True

    # --- Canadian Bar Association (news releases) ---------------------------
    releases_api_url: str = ""
    releases_site_url: str = ""
    releases_article_path: str = "/article/"
    releases_type_id: str = ""
    releases_api_language: str = "en-us"
    releases_page_size: int = Field(default=50, ge=1, le=200)
    releases_max_pages: int = Field(default=10, ge=1)

    # --- U.S. Department of Commerce (reports, via Firecrawl) ---------------
    reports_listing_url: str = ""
    firecrawl_api_key: str = ""
    firecrawl_api_url: str = "https://api.firecrawl.dev/v2/scrape"
    firecrawl_timeout_seconds: float = Field(default=90.0, gt=0)
    firecrawl_max_retries: int = Field(default=2, ge=0, le=5)
    firecrawl_request_delay_seconds: float = Field(default=1.5, ge=0, le=60)
    firecrawl_max_pages_per_run: int = Field(default=6, ge=1, le=50)
    firecrawl_max_detail_scrapes_per_run: int = Field(default=20, ge=0, le=500)
    firecrawl_min_body_words: int = Field(default=30, ge=1)
    firecrawl_cache_dir: str = "data/firecrawl-cache"

    # --- Output -------------------------------------------------------------
    output_dir: str = "data"

    # --- PostgreSQL (the only persistence backend) --------------------------
    database_url: str = ""
    db_host: str = ""
    db_user: str = "lexcrawl"
    db_password: str = ""
    db_name: str = "lexcrawl"
    db_echo: bool = False
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout: float = Field(default=30.0, gt=0)
    db_connect_timeout: int = Field(default=10, ge=1)

    # --- Daily scheduler (APScheduler), started by `lexcrawl schedule` ------
    schedule_enabled: bool = True
    schedule_daily_cron: str = "0 6 * * *"
    schedule_timezone: str = "UTC"

    # --- Guards: pause on an unexpected burst, cap monthly provider spend ---
    anomaly_max_new_items: int = Field(default=10, ge=0)
    firecrawl_monthly_credit_cap: int = Field(default=900, ge=0)

    # --- HTTP API and worker ------------------------------------------------
    api_token: str = ""
    api_title: str = "LexCrawl API"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    job_claim_lease_seconds: int = Field(default=900, ge=1)
    # $ Run queued jobs inside the API process (single-container hosts with no separate worker).$
    embedded_worker: bool = False

    # --- Attachment (PDF) extraction ---------------------------------------
    resource_extraction_attempts: int = Field(default=3, ge=1, le=20)
    resource_max_bytes: int = Field(default=20_000_000, gt=0)
    resource_claim_lease_seconds: int = Field(default=300, ge=1)
    resource_allowed_host_suffixes: list[str] = Field(default_factory=lambda: [".gov", "cba.ca"])

    # --- Change detection ---------------------------------------------------
    document_extraction_attempts: int = Field(default=3, ge=1, le=20)
    reconcile_interval_days: int = Field(default=7, ge=0)

    def database_dsn(self) -> str:
        """### Return the configured DSN, or build one from parts (safe for special chars)."""
        if self.database_url.strip():
            return self.database_url
        if self.db_host.strip():
            return URL.create(
                "postgresql+psycopg",
                username=self.db_user,
                password=self.db_password,
                host=self.db_host,
                port=5432,
                database=self.db_name,
            ).render_as_string(hide_password=False)
        return ""

    def require_database_url(self) -> str:
        """### Return the DSN, failing fast when no database is configured."""
        dsn = self.database_dsn()
        if not dsn.strip():
            raise ValueError("DATABASE_URL (or DB_HOST/DB_USER/DB_PASSWORD/DB_NAME) is not configured")
        return dsn


def required_setting(value: str, *, name: str) -> str:
    """### Return a required setting, failing fast with an actionable message."""
    if not value.strip():
        raise ValueError(f"{name} is not configured; set it in .env (see .env.example)")
    return value.strip()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """### Return the process-wide settings instance."""
    return Settings()
