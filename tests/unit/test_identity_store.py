"""### Unit tests for the JSON identity store."""

from __future__ import annotations

from pathlib import Path

import pytest

from lexcrawl.checks.identities import IdentityStore
from lexcrawl.state_io import StateCorruptionError


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


def test_corrupt_store_is_preserved_and_reported(tmp_path: Path) -> None:
    """### A corrupt state file is preserved aside and raises instead of reading empty."""
    path = tmp_path / "ids.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(StateCorruptionError):
        IdentityStore(path).known("uae")
    assert list(tmp_path.glob("ids.json.corrupt-*"))


def test_json_memory_returns_processed_count(tmp_path: Path) -> None:
    """### JsonDocumentMemory returns the processed count, matching the DB implementation."""
    from lexcrawl.checks.identities import JsonDocumentMemory
    from lexcrawl.models.document import CanonicalDocument, compute_content_hash

    body = "TEXT-BEGIN\nbody"
    document = CanonicalDocument(
        source="uae",
        source_url="https://t.test/a",
        title="T",
        language="en",
        text=body,
        content_hash=compute_content_hash(body),
        extraction_status="complete",
    )
    memory = JsonDocumentMemory(tmp_path / "ids.json")
    # $ Duplicates are recorded once but the return counts processed documents.$
    assert memory.remember("uae", [document, document]) == 2
    assert memory.known("uae") == frozenset({document.identity_key})
