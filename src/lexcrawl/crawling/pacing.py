"""### Request pacing shared by every crawling strategy."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class RequestPacer:
    """### Enforce a minimum interval between request start times."""

    def __init__(
        self,
        min_interval_seconds: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """### Configure the interval with an injectable clock and sleeper."""
        self._min_interval_seconds = max(0.0, min_interval_seconds)
        self._sleep = sleep
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._last_started_at = 0.0

    @property
    def min_interval_seconds(self) -> float:
        """### Return the configured minimum interval."""
        return self._min_interval_seconds

    def wait_for_slot(self) -> None:
        """### Block until the next request is allowed to start."""
        if self._min_interval_seconds <= 0:
            return
        with self._lock:
            elapsed = self._monotonic() - self._last_started_at
            remaining = self._min_interval_seconds - elapsed
            if remaining > 0:
                self._sleep(remaining)
            self._last_started_at = self._monotonic()
