"""### Session-rotation tests for the browser fetcher."""

from __future__ import annotations

import pytest

from lexcrawl.crawling.base import AccessBlockedError, FetchedPage
from lexcrawl.crawling.rotating import RotatingFetcher


class StubSession:
    """### Minimal fetcher that serves only its allowed URLs and blocks the rest."""

    engine_name = "browser"

    def __init__(self, allowed: set[str]) -> None:
        """### Record which URLs this session will serve."""
        self.allowed = allowed
        self.closed = False
        self.calls: list[str] = []

    def _respond(self, url: str) -> FetchedPage:
        """### Return a page for an allowed URL or raise a block."""
        self.calls.append(url)
        if url not in self.allowed:
            raise AccessBlockedError("blocked", url=url, status_code=403, reason="cloudflare")
        return FetchedPage(url=url, status_code=200, content=b"<html></html>")

    def fetch(self, url: str) -> FetchedPage:
        """### Fetch one URL."""
        return self._respond(url)

    def fetch_binary(self, url: str, *, max_bytes: int | None = None) -> FetchedPage:
        """### Fetch one binary payload."""
        return self._respond(url)

    def post_json(self, url: str, *, payload: str) -> FetchedPage:
        """### Post a JSON payload."""
        return self._respond(url)

    def close(self) -> None:
        """### Mark the session closed."""
        self.closed = True


class SessionFactory:
    """### Build stub sessions in order, repeating the last allow-list."""

    def __init__(self, sessions: list[set[str]]) -> None:
        """### Record the per-session allow-lists."""
        self._sessions = sessions
        self.created: list[StubSession] = []

    def __call__(self) -> StubSession:
        """### Return the next stub session."""
        index = min(len(self.created), len(self._sessions) - 1)
        session = StubSession(self._sessions[index])
        self.created.append(session)
        return session


def test_rotation_continues_after_a_block() -> None:
    """### A block closes the session and the retry runs on a fresh one."""
    factory = SessionFactory([{"a"}, {"b"}])
    fetcher = RotatingFetcher(factory, engine_name="browser", max_sessions=5, blocked_session_limit=3)

    assert fetcher.fetch("a").status_code == 200
    assert fetcher.fetch("b").status_code == 200
    assert fetcher.sessions_started == 2
    assert factory.created[0].closed is True
    assert factory.created[1].calls == ["b"]


def test_persistent_block_gives_up_after_the_limit() -> None:
    """### Consecutive refused sessions stop the rotation."""
    factory = SessionFactory([set()])
    fetcher = RotatingFetcher(factory, engine_name="browser", max_sessions=10, blocked_session_limit=3)

    with pytest.raises(AccessBlockedError, match="consecutive fresh browser sessions"):
        fetcher.fetch("a")
    assert fetcher.sessions_started == 3


def test_session_budget_is_bounded() -> None:
    """### The hard session cap is never exceeded."""
    factory = SessionFactory([set()])
    fetcher = RotatingFetcher(factory, engine_name="browser", max_sessions=2, blocked_session_limit=99)

    with pytest.raises(AccessBlockedError, match="budget exhausted"):
        fetcher.fetch("a")
    assert fetcher.sessions_started == 2


def test_a_success_resets_the_consecutive_block_counter() -> None:
    """### A session that succeeds is progress, not a wall."""
    factory = SessionFactory([set(), {"x"}, {"y"}, {"z"}])
    fetcher = RotatingFetcher(factory, engine_name="browser", max_sessions=10, blocked_session_limit=2)

    assert fetcher.fetch("x").status_code == 200
    assert fetcher.fetch("y").status_code == 200
    assert fetcher.fetch("z").status_code == 200
    assert fetcher.sessions_started == 4


def test_close_releases_the_active_session() -> None:
    """### Closing the rotating fetcher closes the live session."""
    factory = SessionFactory([{"a"}])
    fetcher = RotatingFetcher(factory, engine_name="browser", max_sessions=5, blocked_session_limit=3)
    fetcher.fetch("a")
    fetcher.close()
    assert factory.created[0].closed is True
    assert fetcher.sessions_started == 1


def test_fetch_binary_rotates_too() -> None:
    """### Binary fetches use the same rotation policy."""
    factory = SessionFactory([set(), {"file"}])
    fetcher = RotatingFetcher(factory, engine_name="browser", max_sessions=5, blocked_session_limit=3)
    assert fetcher.fetch_binary("file").status_code == 200
    assert fetcher.sessions_started == 2
