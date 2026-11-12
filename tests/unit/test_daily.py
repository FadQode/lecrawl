"""### End-to-end tests for the daily probe-then-crawl runner."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from lexcrawl.checks.identities import IdentityStore
from lexcrawl.checks.runner import _budget_exceeded, run_daily
from lexcrawl.checks.usage import UsageStore
from lexcrawl.config import Settings
from samples import UAE_LISTING_URL, uae_detail_html, uae_listing_html

DETAIL_1 = f"{UAE_LISTING_URL}/article-1"
DETAIL_2 = f"{UAE_LISTING_URL}/article-2"


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    """### Build settings with per-test state files."""
    values: dict[str, object] = {
        "_env_file": None,
        "listings_url": UAE_LISTING_URL,
        "releases_site_url": "https://cba.example.test",
        "crawl_user_agent": "LexCrawl/0.1 (+contact)",
        "crawl_request_delay_seconds": 0,
        "firecrawl_min_body_words": 3,
        "check_state_path": str(tmp_path / "checks.json"),
        "identities_state_path": str(tmp_path / "identities.json"),
        "usage_state_path": str(tmp_path / "usage.json"),
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@respx.mock
def test_daily_probes_then_crawls_then_skips(tmp_path: Path) -> None:
    """### The daily run probes, crawls the change once, then skips known items."""
    listing_route = respx.get(UAE_LISTING_URL).mock(
        return_value=httpx.Response(
            200, content=uae_listing_html(cards=2, next_page=None), headers={"content-type": "text/html"}
        )
    )
    respx.get(DETAIL_1).mock(
        return_value=httpx.Response(200, content=uae_detail_html(), headers={"content-type": "text/html"})
    )
    respx.get(DETAIL_2).mock(
        return_value=httpx.Response(
            200, content=uae_detail_html(title="الخبر الثاني"), headers={"content-type": "text/html"}
        )
    )

    settings = _settings(tmp_path)
    first = run_daily(settings, sources=("uae",))
    assert first[0].outcome == "new_data"
    assert first[0].documents == 2
    assert first[0].credits == 0
    # $ The check and the actor shared one listing fetch (no double credit).$
    assert listing_route.call_count == 1

    second = run_daily(settings, sources=("uae",))
    assert second[0].outcome == "no_change"
    assert second[0].documents == 0

    third = run_daily(settings, sources=("uae",), force=True)
    assert third[0].documents == 0
    assert third[0].skipped_known == 2


@respx.mock
def test_daily_anomaly_skips_crawl(tmp_path: Path) -> None:
    """### An unexpected burst of new items pauses the crawl and does not fetch details."""
    listing_route = respx.get(UAE_LISTING_URL).mock(
        return_value=httpx.Response(
            200, content=uae_listing_html(cards=2, next_page=None), headers={"content-type": "text/html"}
        )
    )
    settings = _settings(tmp_path, anomaly_max_new_items=1)
    # $ Seed a baseline so the guard applies (it is skipped on bootstrap).$
    IdentityStore(Path(settings.identities_state_path)).add("uae_legislation_news", ["seed"])

    results = run_daily(settings, sources=("uae",))

    assert results[0].outcome == "anomaly"
    assert results[0].documents == 0
    assert listing_route.call_count == 1


def test_budget_only_blocks_credit_sources(tmp_path: Path) -> None:
    """### The budget governor blocks only credit-bearing sources, and --force bypasses it."""
    settings = Settings(_env_file=None, firecrawl_monthly_credit_cap=10, usage_state_path=str(tmp_path / "usage.json"))
    usage = UsageStore(tmp_path / "usage.json")
    usage.add("2026-11", 10)

    assert _budget_exceeded("commerce", settings, usage, "2026-11", False)
    assert not _budget_exceeded("uae", settings, usage, "2026-11", False)
    assert not _budget_exceeded("commerce", settings, usage, "2026-11", True)
