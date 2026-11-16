"""### Atomic, corruption-aware JSON state-file I/O.

Reads never silently discard a damaged file: it is preserved for investigation and a
`StateCorruptionError` is raised so the operator notices. Writes are atomic (temp file
plus rename) so a crash cannot leave a half-written state file.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class StateCorruptionError(RuntimeError):
    """### Raised when a state file exists but cannot be parsed."""


def read_json_state(path: Path) -> dict[str, Any]:
    """### Read a JSON object, preserving and reporting a malformed file."""
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        preserved = _preserve(path)
        raise StateCorruptionError(f"state file {path} is unreadable; preserved as {preserved}") from error
    if not isinstance(payload, dict):
        preserved = _preserve(path)
        raise StateCorruptionError(f"state file {path} is not a JSON object; preserved as {preserved}")
    return payload


def write_json_state(path: Path, payload: dict[str, Any]) -> None:
    """### Write a JSON object atomically (temp file in the same directory, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        Path(temporary).replace(path)
    except BaseException:
        _safe_unlink(temporary)
        raise


def _preserve(path: Path) -> Path:
    """### Copy a damaged state file aside so it can be inspected."""
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    target = path.with_name(f"{path.name}.corrupt-{stamp}")
    try:
        shutil.copy2(path, target)
    except OSError:
        return path
    return target


def _safe_unlink(path: str) -> None:
    """### Remove a temporary file, ignoring errors."""
    with contextlib.suppress(OSError):
        Path(path).unlink()
