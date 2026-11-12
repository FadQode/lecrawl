"""### Persisted watermark store for change detection.

Interim JSON-backed store; it will move behind the same interface when the
PostgreSQL persistence layer lands.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StoredWatermark:
    """### The last fingerprint seen for a source and when it was checked."""

    fingerprint: str | None = None
    checked_at: str | None = None


class WatermarkStore:
    """### Read and write per-source fingerprints in a small JSON file."""

    def __init__(self, path: Path) -> None:
        """### Record the state file location."""
        self._path = path

    @property
    def path(self) -> Path:
        """### Return the state file path."""
        return self._path

    def get(self, source: str) -> StoredWatermark:
        """### Return the stored watermark for a source, or an empty one."""
        entry = self._load().get(source)
        if not isinstance(entry, dict):
            return StoredWatermark()
        fingerprint = entry.get("fingerprint")
        checked_at = entry.get("checked_at")
        return StoredWatermark(
            fingerprint=fingerprint if isinstance(fingerprint, str) else None,
            checked_at=checked_at if isinstance(checked_at, str) else None,
        )

    def record(self, source: str, fingerprint: str | None) -> None:
        """### Store the latest fingerprint for a source with a timestamp."""
        state = self._load()
        state[source] = {"fingerprint": fingerprint, "checked_at": datetime.now(tz=UTC).isoformat()}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    def _load(self) -> dict[str, dict[str, object]]:
        """### Load the state file, tolerating absence or corruption."""
        if not self._path.is_file():
            return {}
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {key: value for key, value in payload.items() if isinstance(value, dict)}
