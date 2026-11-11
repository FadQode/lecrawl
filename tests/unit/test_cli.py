"""### Tests for the CLI crawl entry point."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx

from lexcrawl.cli import main
from lexcrawl.config import Settings
from samples import (
    RELEASES_API_URL,
    RELEASES_ARTICLE_PATH,
    RELEASES_SITE_URL,
    RELEASES_TYPE_ID,
    cba_article_html,
    cba_listing_page,
    cba_listing_record,
)


def _settings(tmp_path: Path) -> Settings:
    """### Build settings pointed at the mocked source."""
    return Settings(
        _env_file=None,
        releases_api_url=RELEASES_API_URL,
        releases_site_url=RELEASES_SITE_URL,
        releases_article_path=RELEASES_ARTICLE_PATH,
        releases_type_id=RELEASES_TYPE_ID,
        crawl_request_delay_seconds=0,
        output_dir=str(tmp_path),
    )


@respx.mock
def test_crawl_writes_json_and_exits_zero(tmp_path: Path) -> None:
    """### A successful crawl writes one JSON row per document and returns 0."""
    respx.post(RELEASES_API_URL).mock(
        return_value=httpx.Response(200, content=cba_listing_page([cba_listing_record()]))
    )
    respx.get(f"{RELEASES_SITE_URL}/article/sample-release").mock(
        return_value=httpx.Response(200, content=cba_article_html())
    )

    destination = tmp_path / "out.json"
    exit_code = main(["crawl", "--source", "cba", "--out", str(destination)], settings=_settings(tmp_path))

    assert exit_code == 0
    rows = json.loads(destination.read_text(encoding="utf-8"))
    assert len(rows) == 1
    assert rows[0]["text"].startswith("TEXT-BEGIN")


def test_missing_source_configuration_exits_two(tmp_path: Path) -> None:
    """### A missing required source setting is reported as an operator error."""
    settings = Settings(_env_file=None, releases_api_url="", releases_site_url="", output_dir=str(tmp_path))
    exit_code = main(["crawl", "--source", "cba", "--out", str(tmp_path / "out.json")], settings=settings)
    assert exit_code == 2
