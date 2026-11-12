"""### Tests for the `check` command gating behavior."""

from __future__ import annotations

from pathlib import Path

import httpx
import respx

from lexcrawl.cli import build_parser, run_checks
from lexcrawl.config import Settings
from samples import UAE_LISTING_URL, uae_listing_html

CBA_BASE = "https://cba.example.test"
CBA_SITEMAP = f"{CBA_BASE}/sitemap.xml"
CBA_SITEMAP_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    f"<url><loc>{CBA_BASE}/article/one</loc><lastmod>2026-11-06</lastmod></url>"
    "</urlset>"
)


def _settings(tmp_path: Path) -> Settings:
    """### Build settings pointing the checks at the test hosts."""
    return Settings(
        _env_file=None,
        listings_url=UAE_LISTING_URL,
        releases_site_url=CBA_BASE,
        crawl_user_agent="LexCrawl/0.1 (+contact)",
        crawl_request_delay_seconds=0,
        check_state_path=str(tmp_path / "checks.json"),
    )


@respx.mock
def test_uae_check_reports_new_then_no_change(tmp_path: Path) -> None:
    """### The first UAE check reports new data; an identical second check reports none."""
    respx.get(UAE_LISTING_URL).mock(
        return_value=httpx.Response(
            200, content=uae_listing_html(cards=3, next_page=None), headers={"content-type": "text/html"}
        )
    )
    arguments = build_parser().parse_args(["check", "--source", "uae"])
    assert run_checks(arguments, _settings(tmp_path)) == 1  # new data (no baseline)
    assert run_checks(arguments, _settings(tmp_path)) == 0  # no change
    assert (tmp_path / "checks.json").is_file()


@respx.mock
def test_uae_check_dry_run_does_not_write(tmp_path: Path) -> None:
    """### A dry run never advances the watermark."""
    respx.get(UAE_LISTING_URL).mock(
        return_value=httpx.Response(
            200, content=uae_listing_html(cards=3, next_page=None), headers={"content-type": "text/html"}
        )
    )
    arguments = build_parser().parse_args(["check", "--source", "uae", "--dry-run"])
    assert run_checks(arguments, _settings(tmp_path)) == 1
    assert not (tmp_path / "checks.json").is_file()


@respx.mock
def test_cba_check_reports_new_then_no_change(tmp_path: Path) -> None:
    """### The CBA check fingerprints the sitemap and gates identically."""
    respx.get(CBA_SITEMAP).mock(
        return_value=httpx.Response(200, content=CBA_SITEMAP_XML.encode(), headers={"content-type": "application/xml"})
    )
    arguments = build_parser().parse_args(["check", "--source", "cba"])
    assert run_checks(arguments, _settings(tmp_path)) == 1
    assert run_checks(arguments, _settings(tmp_path)) == 0
