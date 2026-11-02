"""### robots.txt policy tests shared by the crawling strategies."""

from __future__ import annotations

import logging

import pytest

from lexcrawl.crawling.base import RobotsDisallowedError
from lexcrawl.crawling.robots import RobotsFile, RobotsPolicy

USER_AGENT = "LexCrawl-test/0.1"
LISTING_URL = "https://listing.example.test/ar/news"
ARTICLE_URL = "https://listing.example.test/ar/news/example"
ROBOTS_URL = "https://listing.example.test/robots.txt"


class RobotsServer:
    """### Serve a canned robots.txt and count how often it is requested."""

    def __init__(self, result: RobotsFile) -> None:
        """### Store the canned result."""
        self._result = result
        self.requests: list[str] = []

    def read(self, url: str) -> RobotsFile:
        """### Return the canned robots file."""
        self.requests.append(url)
        return self._result


def test_allow_all_policy_permits_fetching() -> None:
    """### An explicit allow-all robots file permits every path."""
    server = RobotsServer(RobotsFile(status_code=200, text="User-agent: *\nDisallow:\n"))
    RobotsPolicy(server.read, user_agent=USER_AGENT).assert_allowed(ARTICLE_URL)


def test_disallowed_path_raises_a_typed_error() -> None:
    """### A disallowed path is refused with the URL attached."""
    server = RobotsServer(RobotsFile(status_code=200, text="User-agent: *\nDisallow: /ar/news/\n"))
    with pytest.raises(RobotsDisallowedError) as error:
        RobotsPolicy(server.read, user_agent=USER_AGENT).assert_allowed(ARTICLE_URL)
    assert error.value.url == ARTICLE_URL


def test_policy_is_read_once_per_host() -> None:
    """### The robots file is cached across requests for the same host."""
    server = RobotsServer(RobotsFile(status_code=200, text="User-agent: *\nDisallow:\n"))
    policy = RobotsPolicy(server.read, user_agent=USER_AGENT)
    policy.assert_allowed(LISTING_URL)
    policy.assert_allowed(ARTICLE_URL)
    assert server.requests == [ROBOTS_URL]


def test_policy_cache_is_bounded_by_host() -> None:
    """### The robots cache evicts old hosts instead of growing without a limit."""
    server = RobotsServer(RobotsFile(status_code=200, text="User-agent: *\nDisallow:\n"))
    policy = RobotsPolicy(server.read, user_agent=USER_AGENT, max_cached_hosts=1)
    policy.assert_allowed(LISTING_URL)
    policy.assert_allowed("https://second.example.test/news")
    policy.assert_allowed(LISTING_URL)
    assert server.requests == [ROBOTS_URL, "https://second.example.test/robots.txt", ROBOTS_URL]


def test_missing_robots_file_is_treated_as_allow_all(caplog: pytest.LogCaptureFixture) -> None:
    """### A 404 robots file falls back to allow-all and is logged."""
    server = RobotsServer(RobotsFile(status_code=404))
    with caplog.at_level(logging.WARNING):
        RobotsPolicy(server.read, user_agent=USER_AGENT).assert_allowed(ARTICLE_URL)
    assert "robots_absent_allow_all" in caplog.text


def test_unreachable_robots_file_fails_closed(caplog: pytest.LogCaptureFixture) -> None:
    """### An unreachable robots file retries then refuses content requests."""
    server = RobotsServer(RobotsFile(error="connection reset"))
    with caplog.at_level(logging.WARNING), pytest.raises(RobotsDisallowedError):
        RobotsPolicy(server.read, user_agent=USER_AGENT, sleep=lambda _: None).assert_allowed(ARTICLE_URL)
    assert len(server.requests) == 3
    assert "robots_unavailable_fail_closed" in caplog.text


def test_server_error_retries_then_fails_closed() -> None:
    """### A robots server error is not treated as a confirmed absent file."""
    server = RobotsServer(RobotsFile(status_code=503))
    policy = RobotsPolicy(server.read, user_agent=USER_AGENT, sleep=lambda _: None)
    with pytest.raises(RobotsDisallowedError):
        policy.assert_allowed(ARTICLE_URL)
    assert len(server.requests) == 3


def test_forbidden_robots_response_fails_closed_without_retry() -> None:
    """### An unexpected forbidden response cannot be treated as missing policy."""
    server = RobotsServer(RobotsFile(status_code=403))
    with pytest.raises(RobotsDisallowedError):
        RobotsPolicy(server.read, user_agent=USER_AGENT, sleep=lambda _: None).assert_allowed(ARTICLE_URL)
    assert len(server.requests) == 1


def test_malformed_robots_file_fails_closed() -> None:
    """### Non-empty malformed policy text cannot silently enable crawling."""
    server = RobotsServer(RobotsFile(status_code=200, text="Disallow: /private"))
    with pytest.raises(RobotsDisallowedError):
        RobotsPolicy(server.read, user_agent=USER_AGENT).assert_allowed(ARTICLE_URL)


def test_user_agent_specific_rules_are_applied() -> None:
    """### A rule aimed at our user-agent token is honoured."""
    server = RobotsServer(RobotsFile(status_code=200, text="User-agent: lexcrawltest\nDisallow: /ar/news/\n"))
    with pytest.raises(RobotsDisallowedError):
        RobotsPolicy(server.read, user_agent="LexCrawlTest/0.1").assert_allowed(ARTICLE_URL)


def test_rules_for_other_agents_do_not_apply_to_us() -> None:
    """### A rule aimed at a different crawler does not restrict us."""
    server = RobotsServer(RobotsFile(status_code=200, text="User-agent: someotherbot\nDisallow: /ar/news/\n"))
    RobotsPolicy(server.read, user_agent=USER_AGENT).assert_allowed(ARTICLE_URL)
