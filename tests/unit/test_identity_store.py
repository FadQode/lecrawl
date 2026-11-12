"""### Unit tests for the JSON identity store."""

from __future__ import annotations

from pathlib import Path

from lexcrawl.checks.identities import IdentityStore


def test_empty_store_has_no_known(tmp_path: Path) -> None:
    """### A fresh store knows nothing."""
    assert IdentityStore(tmp_path / "ids.json").known("uae") == frozenset()


def test_add_and_query(tmp_path: Path) -> None:
    """### Recorded identities are known afterwards and persisted."""
    store = IdentityStore(tmp_path / "ids.json")
    assert store.add("uae", ["a", "b", "a"]) == 2
    assert store.known("uae") == frozenset({"a", "b"})
    assert IdentityStore(tmp_path / "ids.json").known("uae") == frozenset({"a", "b"})


def test_add_is_incremental(tmp_path: Path) -> None:
    """### Adding already-known identities reports zero newly added."""
    store = IdentityStore(tmp_path / "ids.json")
    store.add("uae", ["a", "b"])
    assert store.add("uae", ["b", "c"]) == 1
    assert store.known("uae") == frozenset({"a", "b", "c"})


def test_sources_are_isolated(tmp_path: Path) -> None:
    """### Identities are scoped per source."""
    store = IdentityStore(tmp_path / "ids.json")
    store.add("uae", ["a"])
    assert store.known("cba") == frozenset()


def test_corrupt_store_is_empty(tmp_path: Path) -> None:
    """### A corrupt state file reads as empty rather than raising."""
    path = tmp_path / "ids.json"
    path.write_text("{not json", encoding="utf-8")
    assert IdentityStore(path).known("uae") == frozenset()
