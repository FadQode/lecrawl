"""### Unit tests for CLI helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from lexcrawl.actors.base import ActorRunResult
from lexcrawl.checks.runner import DailySourceResult
from lexcrawl.cli import build_parser, output_path, render_daily, render_summary
from lexcrawl.config import Settings
from lexcrawl.models.document import CanonicalDocument, RawArticle
from lexcrawl.orchestrator import CrawlRunReport


def test_parser_sources() -> None:
    """### The parser accepts crawl/check/daily/schedule/worker/serve commands."""
    parser = build_parser()
    assert parser.parse_args(["crawl", "--source", "commerce"]).source == "commerce"
    assert parser.parse_args(["check", "--source", "all"]).source == "all"
    assert parser.parse_args(["daily", "--force"]).force is True


def test_output_path(tmp_path: Path) -> None:
    """### A directory yields a timestamped file; a file path is used as-is."""
    settings = Settings(_env_file=None, output_dir=str(tmp_path))
    directory = output_path(SimpleNamespace(out=None, source="cba"), settings)  # type: ignore[arg-type]
    assert directory.parent == tmp_path and directory.suffix == ".json"
    explicit = output_path(SimpleNamespace(out=tmp_path / "x.json", source="cba"), settings)  # type: ignore[arg-type]
    assert explicit == tmp_path / "x.json"


def test_render_summary() -> None:
    """### A run summary renders status and per-source counters."""
    result = ActorRunResult(source="s", documents=[CanonicalDocument.from_raw(RawArticle(source="s", url="https://x.test/a", title="T", language="en", paragraphs=["b"]))])
    text = render_summary(CrawlRunReport(results=[result]))
    assert "status=success" in text and "s: status=success" in text


def test_render_daily() -> None:
    """### A daily result renders its outcome."""
    now = datetime.now(tz=UTC)
    result = DailySourceResult(
        source="s", outcome="no_change", status="success", documents=0, inserted=0, updated=0, discovered=0,
        skipped_known=0, failed=0, resources=0, pending=0, credits=0, duration_seconds=0.1, detail="x",
        pagination_complete=True, errors=(), started_at=now, finished_at=now,
    )
    assert "no_change" in render_daily(result)
