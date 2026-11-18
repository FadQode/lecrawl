"""### Engine construction for the persistence layer."""

from __future__ import annotations

from sqlalchemy import URL, Engine, create_engine, make_url

from lexcrawl.config import Settings

POSTGRESQL_BACKEND = "postgresql"


def mask_dsn(url: str | URL) -> str:
    """### Render a database URL without credentials for logs and errors."""
    parsed = make_url(url) if isinstance(url, str) else url
    return parsed.render_as_string(hide_password=True)


def resolve_database_url(settings: Settings) -> str:
    """### Return the configured DSN (delegates to `Settings.database_dsn`)."""
    return settings.database_dsn()


def database_configured(settings: Settings) -> bool:
    """### Report whether a database DSN can be resolved."""
    return bool(settings.database_dsn().strip())


def create_db_engine(settings: Settings) -> Engine:
    """### Build a lazy SQLAlchemy engine from validated settings."""
    url = make_url(settings.require_database_url())
    options: dict[str, object] = {"echo": settings.db_echo, "pool_pre_ping": True}
    if url.get_backend_name() == POSTGRESQL_BACKEND:
        # $ PostgreSQL keeps a bounded pool and an explicit connection timeout.$
        options.update(
            {
                "pool_size": settings.db_pool_size,
                "max_overflow": settings.db_max_overflow,
                "pool_timeout": settings.db_pool_timeout,
                "connect_args": {"connect_timeout": settings.db_connect_timeout},
            }
        )
    return create_engine(url, **options)
