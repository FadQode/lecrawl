"""### Monthly provider-credit usage, tracked in a small, atomically written JSON file.

Interim store; it moves behind the same interface when the PostgreSQL layer lands.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from lexcrawl.state_io import read_json_state, write_json_state


def current_period(now: datetime | None = None) -> str:
    """### Return the current UTC calendar month as a `YYYY-MM` key."""
    return (now or datetime.now(tz=UTC)).strftime("%Y-%m")


class UsageStore:
    """### Accumulate provider credits spent per calendar month."""

    def __init__(self, path: Path) -> None:
        """### Record the state file location."""
        self._path = path

    @property
    def path(self) -> Path:
        """### Return the state file path."""
        return self._path

    def spent(self, period: str) -> int:
        """### Return the credits recorded for a period."""
        value = self._load().get(period, 0)
        return value if isinstance(value, int) and value >= 0 else 0

    def add(self, period: str, credits: int) -> int:
        """### Add credits to a period and return the new total."""
        if credits <= 0:
            return self.spent(period)
        state = self._load()
        total = self.spent(period) + credits
        state[period] = total
        write_json_state(self._path, state)
        return total

    def _load(self) -> dict[str, int]:
        """### Load the state file, surfacing (not swallowing) corruption."""
        payload = read_json_state(self._path)
        return {key: value for key, value in payload.items() if isinstance(value, int) and value >= 0}
