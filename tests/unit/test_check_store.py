"""### Unit tests for the JSON watermark store."""

from __future__ import annotations

from pathlib import Path

import pytest

from lexcrawl.checks.store import WatermarkStore
from lexcrawl.state_io import StateCorruptionError


def test_missing_state_reads_empty(tmp_path: Path) -> None:
    """### An absent state file yields an empty watermark."""
    store = WatermarkStore(tmp_path / "checks.json")
    assert store.get("uae").fingerprint is None
    assert store.get("uae").checked_at is None


def test_record_then_get_round_trips(tmp_path: Path) -> None:
    """### A recorded fingerprint is persisted and read back."""
    store = WatermarkStore(tmp_path / "checks.json")
    store.record("uae", "abc")
    again = WatermarkStore(tmp_path / "checks.json")
    assert again.get("uae").fingerprint == "abc"
    assert again.get("uae").checked_at is not None


def test_corrupt_state_is_preserved_and_reported(tmp_path: Path) -> None:
    """### A corrupt state file is preserved aside and raises instead of reading empty."""
    path = tmp_path / "checks.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(StateCorruptionError):
        WatermarkStore(path).get("uae")
    assert list(tmp_path.glob("checks.json.corrupt-*"))
