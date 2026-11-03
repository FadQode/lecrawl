"""### CLI tests: wiring, rendering, output file, and exit codes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from lexcrawl.actors.base import ActorRunResult
from lexcrawl.actors.uae_legislation import UaeLegislationActor
from lexcrawl.cli import build_actor, build_actors, build_fetcher, build_parser, main, output_path, render_summary
from lexcrawl.config import Settings
from lexcrawl.crawling.browser import BrowserFetcher
from lexcrawl.crawling.http import HttpFetcher
from lexcrawl.models.document import CanonicalDocument, RawArticle
from lexcrawl.orchestrator import CrawlRunReport

from ..samples import (
    LISTING_URL,
    RELEASES_API_URL,
    RELEASES_SITE_URL,
    RELEASES_TYPE_ID,
    REPORTS_API_URL,
    REPORTS_ARTICLE_PATHS,
    REPORTS_HOST,
)


def build_settings(**overrides: object) -> Settings:
    """### Build settings wired to the sample source configuration."""
    values: dict[str, object] = {
        "listings_url": LISTING_URL,
        "listings_language": "ar",
        "reports_listing_url": "https://www.commerce.gov/data-and-reports/reports",
        "reports_api_url": REPORTS_API_URL,
        "reports_api_key": "TEST_KEY",
        "reports_article_host": REPORTS_HOST,
        "reports_article_paths": ",".join(REPORTS_ARTICLE_PATHS),
        "reports_news_types": "Reports",
        "releases_api_url": RELEASES_API_URL,
        "releases_site_url": RELEASES_SITE_URL,
        "releases_api_language": "en-us",
        "releases_type_id": RELEASES_TYPE_ID,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def parse(arguments: list[str]) -> argparse.Namespace:
    """### Parse CLI arguments the way the entry point does."""
    return build_parser().parse_args(arguments)


def sample_document(index: int) -> CanonicalDocument:
    """### Build one canonical document for CLI assertions."""
    return CanonicalDocument.from_raw(
        RawArticle(
            source="uae_legislation_news",
            url=f"{LISTING_URL}/article-{index}",
            title="عنوان الخبر",
            language="ar",
            paragraphs=["الفقرة الأولى"],
        )
    )


def build_report(*, documents: int, errors: list[str] | None = None) -> CrawlRunReport:
    """### Build a canned run report for presentation tests."""
    result = ActorRunResult(source="uae_legislation_news")
    result.documents.extend(sample_document(index) for index in range(documents))
    for error in errors or []:
        result.add_error(error)
    result.listing_pages_attempted = 2
    result.listing_pages_succeeded = 2
    result.detail_pages_attempted = max(documents, 1)
    result.detail_pages_succeeded = documents
    result.articles_discovered = documents
    result.pagination_complete = True
    return CrawlRunReport(results=[result], duration_seconds=0.5)


def test_build_actor_uses_settings_and_cli_overrides() -> None:
    """### Actor wiring prefers CLI values and falls back to settings."""
    settings = build_settings()
    default_actor = build_actor("uae", parse(["crawl"]), settings)
    assert isinstance(default_actor, UaeLegislationActor)
    assert default_actor.listing_url == settings.listings_url
    assert default_actor.language == settings.listings_language
    assert default_actor.max_listing_pages == settings.crawl_max_listing_pages
    assert default_actor.concurrency == settings.crawl_concurrency_per_source

    overridden = build_actor("uae", parse(["crawl", "--max-pages", "3", "--concurrency", "1"]), settings)
    assert isinstance(overridden, UaeLegislationActor)
    assert overridden.max_listing_pages == 3
    assert overridden.concurrency == 1


def test_build_fetcher_selects_the_configured_engine() -> None:
    """### The CLI picks the crawling strategy from the flag or settings."""
    settings = build_settings()
    assert isinstance(build_fetcher(parse(["crawl"]), settings), HttpFetcher)
    assert isinstance(build_fetcher(parse(["crawl", "--engine", "browser"]), settings), BrowserFetcher)
    settings_with_browser = Settings(_env_file=None, crawl_engine="browser")
    assert isinstance(build_fetcher(parse(["crawl"]), settings_with_browser), BrowserFetcher)


def test_browser_engine_is_single_threaded() -> None:
    """### The browser engine forces one detail-extraction thread."""
    settings = build_settings()
    arguments = parse(["crawl", "--engine", "browser", "--concurrency", "8"])
    actor = build_actor("uae", arguments, settings)
    assert isinstance(actor, UaeLegislationActor)
    assert actor.concurrency == 1
    http_actor = build_actor("uae", parse(["crawl", "--concurrency", "8"]), settings)
    assert isinstance(http_actor, UaeLegislationActor)
    assert http_actor.concurrency == 8


def test_build_actors_expands_the_all_selector() -> None:
    """### `--source all` builds every registered source."""
    actors = build_actors(parse(["crawl", "--source", "all"]), build_settings())
    assert [actor.name for actor in actors] == ["uae_legislation_news", "commerce_reports", "cba_news_releases"]


def test_build_actor_rejects_unknown_sources() -> None:
    """### An unregistered source name is a programming error."""
    with pytest.raises(ValueError, match="unknown source"):
        build_actor("not-a-source", parse(["crawl"]), build_settings())


def test_missing_source_configuration_is_reported_without_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """### An unset source location exits with an actionable message."""
    assert main(["crawl", "--source", "uae", "--log-format", "console"], Settings(_env_file=None)) == 2
    assert "LISTINGS_URL is not configured" in capsys.readouterr().out


def test_render_summary_lists_counters_and_errors() -> None:
    """### The summary shows measured counters and actionable errors."""
    text = render_summary(build_report(documents=2, errors=["article fetch failed: boom"]))
    assert "status=partial_failure" in text
    assert "documents=2" in text
    assert "listing=2/2" in text
    assert "details=2/2" in text
    assert "pagination_complete=True" in text
    assert "error: article fetch failed: boom" in text


def test_main_returns_zero_on_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """### A successful run exits zero, prints the summary, and writes records."""
    monkeypatch.setattr("lexcrawl.cli.run_crawl", lambda actors, fetcher: build_report(documents=20))
    assert main(["crawl", "--source", "uae", "--out", str(tmp_path), "--log-format", "console"], build_settings()) == 0
    output = capsys.readouterr().out
    assert "status=success" in output
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_main_returns_one_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """### A failed run exits non-zero and leaves no empty JSON behind."""
    monkeypatch.setattr("lexcrawl.cli.run_crawl", lambda actors, fetcher: build_report(documents=0, errors=["blocked"]))
    assert main(["crawl", "--source", "uae", "--out", str(tmp_path), "--log-format", "console"], build_settings()) == 1
    output = capsys.readouterr().out
    assert "error: blocked" in output
    assert "nothing written" in output
    assert list(tmp_path.glob("*.json")) == []


def test_main_writes_documents_as_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """### `--out` writes the collected documents without escaping Arabic."""
    output = tmp_path / "documents.json"
    monkeypatch.setattr("lexcrawl.cli.run_crawl", lambda actors, fetcher: build_report(documents=2))
    arguments = ["crawl", "--source", "uae", "--out", str(output), "--log-format", "console"]
    assert main(arguments, build_settings()) == 0
    capsys.readouterr()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert len(payload) == 2
    assert payload[0]["text"].startswith("TEXT-BEGIN")
    assert "الفقرة الأولى" in payload[0]["text"]
    assert payload[0]["source_url"].startswith(LISTING_URL)


def test_output_path_uses_a_timestamped_file_for_directories(tmp_path: Path) -> None:
    """### A directory destination receives a timestamped JSON file."""
    settings = build_settings()
    destination = output_path(parse(["crawl", "--source", "commerce", "--out", str(tmp_path)]), settings)
    assert destination.parent == tmp_path
    assert destination.suffix == ".json"
    assert destination.name.endswith("-commerce.json")


def test_output_path_defaults_to_the_configured_directory(tmp_path: Path) -> None:
    """### Without --out the configured output directory is used."""
    settings = Settings(_env_file=None, output_dir=str(tmp_path))
    destination = output_path(parse(["crawl", "--source", "uae"]), settings)
    assert destination.parent == tmp_path
    assert destination.name.endswith("-uae.json")


def test_output_path_honours_an_explicit_file(tmp_path: Path) -> None:
    """### An explicit file path is used verbatim."""
    settings = build_settings()
    target = tmp_path / "records.json"
    assert output_path(parse(["crawl", "--out", str(target)]), settings) == target


def test_main_writes_a_timestamped_file_into_a_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """### A successful run leaves one JSON file with the collected rows."""
    monkeypatch.setattr("lexcrawl.cli.run_crawl", lambda actors, fetcher: build_report(documents=2))
    destination = ["crawl", "--source", "commerce", "--out", str(tmp_path), "--log-format", "console"]
    assert main(destination, build_settings()) == 0
    written = list(tmp_path.glob("*.json"))
    assert len(written) == 1
    payload = json.loads(written[0].read_text(encoding="utf-8"))
    assert len(payload) == 2
    assert payload[0]["text"].startswith("TEXT-BEGIN")
    assert "records written to" in capsys.readouterr().out


def test_bilingual_flag_and_secondary_listing_reach_the_actor() -> None:
    """### The localization options are wired from the CLI and settings."""
    settings = build_settings(crawl_bilingual=False, listings_secondary_url="https://listing.example.test/en/news")
    without_flag = build_actor("uae", parse(["crawl"]), settings)
    with_flag = build_actor("uae", parse(["crawl", "--bilingual"]), settings)

    assert isinstance(without_flag, UaeLegislationActor)
    assert isinstance(with_flag, UaeLegislationActor)
    assert without_flag.bilingual is False
    assert with_flag.bilingual is True
    assert with_flag.secondary_listing_url == "https://listing.example.test/en/news"


def test_crawl_persist_calls_the_persistence_service(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """### `--persist` hands the run report to persistence after the crawl."""
    report = build_report(documents=2)
    monkeypatch.setattr("lexcrawl.cli.run_crawl", lambda actors, fetcher: report)
    persisted: list[object] = []
    monkeypatch.setattr("lexcrawl.cli.persist_report", lambda value, settings: persisted.append(value) or 0)

    arguments = ["crawl", "--source", "uae", "--persist", "--out", str(tmp_path), "--log-format", "console"]
    assert main(arguments, build_settings()) == 0
    assert persisted == [report]
    assert "status=success" in capsys.readouterr().out


def test_crawl_persist_failure_returns_non_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """### A persistence failure is reported and fails the command."""
    monkeypatch.setattr("lexcrawl.cli.run_crawl", lambda actors, fetcher: build_report(documents=1))
    monkeypatch.setattr("lexcrawl.cli.persist_report", lambda value, settings: 1)

    arguments = ["crawl", "--source", "uae", "--persist", "--out", str(tmp_path), "--log-format", "console"]
    assert main(arguments, build_settings()) == 1
    assert "records written to" in capsys.readouterr().out


def test_crawl_without_persist_does_not_touch_the_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """### Database persistence stays opt-in."""
    monkeypatch.setattr("lexcrawl.cli.run_crawl", lambda actors, fetcher: build_report(documents=1))

    def forbidden(*_: object) -> int:
        """### Fail if persistence is attempted."""
        raise AssertionError("persistence must not run without --persist")

    monkeypatch.setattr("lexcrawl.cli.persist_report", forbidden)
    arguments = ["crawl", "--source", "uae", "--out", str(tmp_path), "--log-format", "console"]
    assert main(arguments, build_settings()) == 0


def test_commerce_actor_uses_firecrawl_with_explicit_bootstrap() -> None:
    """### The Commerce actor is wired to Firecrawl and an explicit bootstrap flag."""
    from lexcrawl.actors.commerce import CommerceReportsActor

    settings = build_settings()
    default_actor = build_actor("commerce", parse(["crawl"]), settings)
    bootstrap_actor = build_actor("commerce", parse(["crawl", "--bootstrap"]), settings)

    assert isinstance(default_actor, CommerceReportsActor)
    assert isinstance(bootstrap_actor, CommerceReportsActor)
    assert default_actor.bootstrap is False
    assert bootstrap_actor.bootstrap is True
    assert default_actor.firecrawl is not None
    assert default_actor.listing_url == settings.reports_listing_url
