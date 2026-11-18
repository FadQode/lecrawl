"""### In-process queue runner: lets the API execute queued crawl jobs itself.

Free hosts run a single container, so there may be no separate `lexcrawl worker`
process. When `EMBEDDED_WORKER=true`, the API owns one :class:`QueueDrainer`: every
`POST /jobs` (and API startup) "kicks" it, and it runs queued jobs on a background
thread until the queue is empty, then stops. It never polls while idle, so an idle
API keeps the database free to scale to zero.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from lexcrawl.logging import get_logger

LOGGER = get_logger(__name__)


class QueueDrainer:
    """### Run queued jobs on one background thread until the queue is empty."""

    def __init__(self, run_job: Callable[[], bool]) -> None:
        """### Store the callable that runs one job and reports whether it found one."""
        self._run_job = run_job
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        # $ Set by a kick that arrives while a drain is running, so that drain makes one
        # $ more pass instead of exiting and stranding the newly queued job.$
        self._requested = False
        self._stopping = False

    @property
    def running(self) -> bool:
        """### Report whether a drain thread is currently active."""
        with self._lock:
            return self._thread is not None

    def kick(self) -> bool:
        """### Start draining the queue; return False when a drain was already running."""
        with self._lock:
            if self._stopping:
                return False
            self._requested = True
            if self._thread is not None:
                return False
            thread = threading.Thread(target=self._drain, name="lexcrawl-queue-drainer", daemon=True)
            self._thread = thread
        thread.start()
        return True

    def stop(self) -> None:
        """### Ask the drainer to stop after the job it is running (does not block)."""
        with self._lock:
            self._stopping = True

    def join(self, timeout: float | None = None) -> None:
        """### Wait for the current drain thread to finish (used by tests and shutdown)."""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _drain(self) -> None:
        """### Run jobs until one pass finds the queue empty and no new kick arrived."""
        while True:
            with self._lock:
                # $ Deciding to exit and clearing the thread happen under one lock, so a
                # $ concurrent kick either sees this thread (and is picked up) or starts a new one.$
                if self._stopping or not self._requested:
                    self._thread = None
                    return
                self._requested = False
            while not self._stopping:
                try:
                    processed = self._run_job()
                except Exception:  # $ A failing pass must not kill the thread silently.$
                    LOGGER.exception("embedded_worker_pass_failed")
                    processed = False
                if not processed:
                    break
