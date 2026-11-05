"""### Session-rotating fetcher that continues after a bot-protection block."""

from __future__ import annotations

from collections.abc import Callable

from lexcrawl.crawling.base import AccessBlockedError, FetchedPage, Fetcher
from lexcrawl.logging import get_logger

LOGGER = get_logger(__name__)


class RotatingFetcher:
    """### Wrap a session factory and open a fresh session after each block.

    The wrapped strategy is used normally until it reports `AccessBlockedError`. At that
    point the current session is closed, a fresh one is opened, and the same request is
    retried, so a caller keeps its position and the crawl continues where it left off.
    Rotation stops when the session budget is spent or several consecutive fresh sessions
    are refused without a single success, which means the source is walled off entirely.
    """

    def __init__(
        self,
        factory: Callable[[], Fetcher],
        *,
        engine_name: str,
        max_sessions: int,
        blocked_session_limit: int = 3,
    ) -> None:
        """### Configure the session factory and the rotation budget."""
        self._factory = factory
        self._engine_name = engine_name
        self._max_sessions = max(1, max_sessions)
        self._blocked_session_limit = max(1, blocked_session_limit)
        self._inner: Fetcher | None = None
        self._sessions = 0
        self._session_had_success = False
        self._blocked_sessions = 0

    @property
    def engine_name(self) -> str:
        """### Report the wrapped strategy name."""
        return self._engine_name

    @property
    def sessions_started(self) -> int:
        """### Report how many sessions have been opened so far."""
        return self._sessions

    def fetch(self, url: str) -> FetchedPage:
        """### Fetch one URL, rotating to a fresh session after a block."""
        return self._run(lambda fetcher: fetcher.fetch(url), url)

    def fetch_binary(self, url: str, *, max_bytes: int | None = None) -> FetchedPage:
        """### Fetch one binary payload, rotating after a block."""
        return self._run(lambda fetcher: fetcher.fetch_binary(url, max_bytes=max_bytes), url)

    def post_json(self, url: str, *, payload: str) -> FetchedPage:
        """### Post a JSON payload, rotating after a block."""
        return self._run(lambda fetcher: fetcher.post_json(url, payload=payload), url)

    def close(self) -> None:
        """### Close the active session, if any."""
        self._rotate()

    def __enter__(self) -> RotatingFetcher:
        """### Support use as a context manager."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """### Close the active session when leaving the context."""
        self.close()

    def _run(self, operation: Callable[[Fetcher], FetchedPage], url: str) -> FetchedPage:
        """### Run one operation, reopening a fresh session after each block."""
        while True:
            fetcher = self._current(url)
            try:
                result = operation(fetcher)
            except AccessBlockedError:
                self._note_block(url)
                self._rotate()
                continue
            self._session_had_success = True
            self._blocked_sessions = 0
            return result

    def _current(self, url: str) -> Fetcher:
        """### Return the live session, opening a fresh one when none is active."""
        if self._inner is None:
            if self._sessions >= self._max_sessions:
                raise AccessBlockedError(
                    "browser session budget exhausted before the fetch completed",
                    url=url,
                    status_code=403,
                    reason="session budget",
                )
            self._inner = self._factory()
            self._sessions += 1
            self._session_had_success = False
            LOGGER.info("rotating_fetcher_session_opened", extra={"session": self._sessions})
        return self._inner

    def _rotate(self) -> None:
        """### Close the current session so the next call starts a fresh one."""
        if self._inner is not None:
            self._inner.close()
            self._inner = None

    def _note_block(self, url: str) -> None:
        """### Count a refused session and stop rotating once the source is fully walled off."""
        if self._session_had_success:
            # $ A session that earned a fetch before blocking is progress, not a wall.$
            self._blocked_sessions = 1
            return
        self._blocked_sessions += 1
        if self._blocked_sessions >= self._blocked_session_limit:
            raise AccessBlockedError(
                "consecutive fresh browser sessions were refused",
                url=url,
                status_code=403,
                reason="persistent block",
            )
