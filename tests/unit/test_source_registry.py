"""### Unit tests for the source registry (adding a source is one entry)."""

from __future__ import annotations

import pytest

from lexcrawl.config import Settings
from lexcrawl.factory import build_resource_fetcher
from lexcrawl.sources import (
    REGISTRY,
    SOURCE_NAMES,
    build_actor,
    build_check,
    build_scraper,
    redirect_host_allowlist,
    spec_for,
)
from samples import (
    COMMERCE_LISTING_URL,
    RELEASES_API_URL,
    RELEASES_SITE_URL,
    RELEASES_TYPE_ID,
    UAE_LISTING_URL,
)


def _settings() -> Settings:
    """### Settings pointing at example hosts."""
    return Settings(
        _env_file=None,
        listings_url=UAE_LISTING_URL,
        releases_api_url=RELEASES_API_URL,
        releases_site_url=RELEASES_SITE_URL,
        releases_type_id=RELEASES_TYPE_ID,
        reports_listing_url=COMMERCE_LISTING_URL,
    )


def test_registry_names_match_specs() -> None:
    """### The public source names are exactly the registry keys."""
    assert SOURCE_NAMES == tuple(REGISTRY) == ("uae", "commerce", "cba")
    for name, spec in REGISTRY.items():
        assert spec.name == name
        assert callable(spec.build_scraper)
        assert callable(spec.build_check)
        assert callable(spec.build_actor)


def test_unknown_source_raises() -> None:
    """### An unregistered source fails with a clear error."""
    with pytest.raises(ValueError, match="unknown source"):
        spec_for("nope")


def test_every_source_builds() -> None:
    """### Every registered source builds a check and an actor."""
    settings = _settings()
    fetcher = build_resource_fetcher(settings)
    for name in SOURCE_NAMES:
        scraper = build_scraper(name, settings)
        check = build_check(name, settings, scraper=scraper, http_fetcher=fetcher)
        actor = build_actor(name, settings, scraper=scraper, http_fetcher=fetcher, max_pages=1)
        assert check.name and actor.name


def test_cba_has_no_scraper() -> None:
    """### CBA is fetched over plain HTTP, so it has no scraper."""
    assert build_scraper("cba", _settings()) is None
    assert build_scraper("uae", _settings()) is not None
    assert build_scraper("commerce", _settings()) is not None


def test_redirect_hosts_aggregate_every_source() -> None:
    """### Redirect hosts include each registered source's hosts."""
    hosts = redirect_host_allowlist(_settings())
    assert "releases.example.test" in hosts
    assert "commerce.test" in hosts or "www.commerce.test" in hosts


def test_registry_matches_legacy_source_seed() -> None:
    """### The registry carries the DB identity for every source (drives sources seeding)."""
    from lexcrawl.repositories.sources import SEED_SOURCES

    rows = {(spec.code, spec.display_name, spec.base_url) for spec in REGISTRY.values()}
    assert rows == set(SEED_SOURCES)

