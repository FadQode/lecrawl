"""### Unit tests for runner status/decision helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from lexcrawl.checks import runner
from lexcrawl.checks.base import CheckProbe
from lexcrawl.checks.change_detection import Decision
from lexcrawl.config import Settings


def _run(**overrides: object) -> SimpleNamespace:
    """### Build a minimal actor-result stand-in."""
    values: dict[str, object] = {
        "status": "success",
        "pagination_complete": True,
        "records_without_text": 0,
        "records_missing_required_fields": 0,
        "detail_pages_failed": 0,
        "listing_pages_failed": 0,
        "documents": [object(), object()],
        "records_skipped_known": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_skipped_check_is_success() -> None:
    """### A check with no crawl is a success."""
    assert runner._source_status("no_change", None) == "success"


def test_clean_crawl_is_success() -> None:
    """### A clean, fully-paginated crawl is a success."""
    assert runner._source_status("new_data", _run()) == "success"


def test_failed_reconcile_is_not_success() -> None:
    """### A failed reconcile crawl under no_change is partial."""
    run = _run(status="failed", pagination_complete=False, listing_pages_failed=1)
    assert runner._source_status("no_change", run) == "partial_failure"


def test_failed_detail_is_partial() -> None:
    """### A crawl with a failed extraction is partial."""
    assert runner._source_status("new_data", _run(records_without_text=1)) == "partial_failure"


def test_all_failed_is_failed() -> None:
    """### Nothing ingested and nothing skipped is a failure."""
    assert runner._source_status("new_data", _run(status="failed", documents=[], records_skipped_known=0)) == "failed"


def test_failed_count() -> None:
    """### failed_count covers documents without text plus listing pages."""
    assert runner._failed_count(_run(records_without_text=2, listing_pages_failed=1)) == 3


def test_aggregate_status() -> None:
    """### Per-source statuses aggregate into a run status."""
    assert runner._aggregate_status([SimpleNamespace(status="success")]) == "success"
    mixed = [SimpleNamespace(status="success"), SimpleNamespace(status="partial_failure")]
    assert runner._aggregate_status(mixed) == "partial_failure"
    assert runner._aggregate_status([SimpleNamespace(status="failed")]) == "failed"
    assert runner._aggregate_status([]) == "failed"


def _decision(processed_at: datetime | None) -> Decision:
    """### Build a change decision with a given processed_at."""
    probe = CheckProbe(source="s", ok=True, fingerprint="f", summary="", duration_seconds=0.0)
    return Decision(source="s", outcome="no_change", probe=probe, processed_fingerprint="f", processed_at=processed_at)


def test_reconcile_due() -> None:
    """### Reconciliation is due only for an old processed_at and enabled interval."""
    settings = Settings(_env_file=None, reconcile_interval_days=7)
    assert runner._reconcile_due(_decision(datetime.now(tz=UTC) - timedelta(days=30)), settings) is True
    assert runner._reconcile_due(_decision(datetime.now(tz=UTC)), settings) is False
    assert runner._reconcile_due(_decision(None), settings) is False
    disabled = Settings(_env_file=None, reconcile_interval_days=0)
    assert runner._reconcile_due(_decision(datetime.now(tz=UTC) - timedelta(days=30)), disabled) is False
