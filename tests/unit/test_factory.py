"""### Unit tests for the composition root (factory)."""

from __future__ import annotations

import pytest

from lexcrawl import factory
from lexcrawl.checks.checkpoints import DbCheckpointStore
from lexcrawl.checks.run_records import DbRunRecorder
from lexcrawl.config import Settings
from lexcrawl.crawling.http import HttpFetcher
from lexcrawl.factory import Runtime
from lexcrawl.repositories.memory import DbDocumentMemory
from samples import (
    COMMERCE_LISTING_URL,
    RELEASES_API_URL,
    RELEASES_SITE_URL,
    RELEASES_TYPE_ID,
    UAE_LISTING_URL,
)


def _settings(**overrides: object) -> Settings:
    """### DB-backend settings pointing at example hosts."""
    values: dict[str, object] = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://u:p@localhost:5432/db",
        "listings_url": UAE_LISTING_URL,
        "releases_api_url": RELEASES_API_URL,
        "releases_site_url": RELEASES_SITE_URL,
        "releases_type_id": RELEASES_TYPE_ID,
        "reports_listing_url": COMMERCE_LISTING_URL,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_db_builders_share_engine_and_require_url() -> None:
    """### DB builders accept an injected engine; a missing URL fails."""
    settings = _settings()
    memory = factory.build_document_memory(settings)
    assert isinstance(memory, DbDocumentMemory)
    memory.close()
    assert isinstance(factory.build_checkpoint_store(settings), DbCheckpointStore)
    assert isinstance(factory.build_run_recorder(settings), DbRunRecorder)
    with pytest.raises(ValueError):
        factory.build_document_memory(_settings(database_url=""))


def test_scraper_and_runtime() -> None:
    """### Scrapers build per source; the runtime bundles components."""
    settings = _settings()
    assert factory.build_scraper("cba", settings) is None
    assert factory.build_scraper("commerce", settings) is not None
    assert factory.build_scraper("uae", settings) is not None
    runtime = factory.build_runtime(settings)
    assert isinstance(runtime, Runtime)
    assert isinstance(runtime.memory, DbDocumentMemory)
    runtime.close()


def test_checks_and_actors_build() -> None:
    """### Checks and actors build for each source."""
    settings = _settings()
    fetcher = factory.build_http_fetcher(settings)
    for name in ("cba", "commerce", "uae"):
        scraper = factory.build_scraper(name, settings)
        check = factory.build_check(name, settings, scraper=scraper, http_fetcher=fetcher)
        actor = factory.build_actor(name, settings, scraper=scraper, http_fetcher=fetcher, max_pages=1)
        assert check.name and actor.name
    assert isinstance(factory.build_resource_fetcher(settings), HttpFetcher)
    fetcher.close()


def test_helpers() -> None:
    """### Host allowlists, sitemap URL, and unknown-source errors."""
    settings = _settings()
    hosts = factory.redirect_host_allowlist(settings)
    assert "releases.example.test" in hosts
    assert factory.cba_sitemap_url(settings) == "https://releases.example.test/sitemap.xml"
    assert factory.uae_allowed_hosts(settings)
    with pytest.raises(ValueError):
        factory.build_check("nope", settings, scraper=None, http_fetcher=factory.build_http_fetcher(settings))
    with pytest.raises(ValueError):
        factory.build_actor("nope", settings, scraper=None, http_fetcher=factory.build_http_fetcher(settings))
