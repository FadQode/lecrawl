"""### robots.txt policy enforcement shared by every crawling strategy."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from lexcrawl.crawling.base import RETRYABLE_STATUS_CODES, RobotsDisallowedError
from lexcrawl.crawling.retry import retry_delay
from lexcrawl.logging import get_logger


@dataclass(frozen=True, slots=True)
class RobotsFile:
    """### Result of retrieving one host's robots.txt."""

    status_code: int | None = None
    text: str | None = None
    error: str | None = None
    retry_after_seconds: float | None = None


class RobotsPolicy:
    """### Cache and apply each host's published robots policy."""

    def __init__(
        self,
        read_file: Callable[[str], RobotsFile],
        *,
        user_agent: str,
        max_retries: int = 2,
        backoff_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        max_cached_hosts: int = 256,
    ) -> None:
        """### Store the retrieval strategy and the identity used for evaluation."""
        self._read_file = read_file
        self._user_agent = user_agent
        self._max_retries = max(0, max_retries)
        self._backoff_seconds = max(0.0, backoff_seconds)
        self._sleep = sleep
        self._parsers: OrderedDict[str, RobotFileParser | None] = OrderedDict()
        self._max_cached_hosts = max(1, max_cached_hosts)
        self._logger = get_logger(__name__)

    def assert_allowed(self, url: str) -> None:
        """### Refuse a URL that the host's robots policy disallows."""
        parser = self._parser_for(url)
        if parser is not None and not parser.can_fetch(self._user_agent, url):
            raise RobotsDisallowedError(f"robots.txt disallows {url}", url=url)

    def _parser_for(self, url: str) -> RobotFileParser | None:
        """### Return the cached robots parser for the URL's host."""
        origin = urlparse(url)
        host_key = f"{origin.scheme}://{origin.netloc}"
        if host_key in self._parsers:
            self._parsers.move_to_end(host_key)
        else:
            self._parsers[host_key] = self._load_parser(f"{host_key}/robots.txt")
            if len(self._parsers) > self._max_cached_hosts:
                self._parsers.popitem(last=False)
        return self._parsers[host_key]

    def _load_parser(self, robots_url: str) -> RobotFileParser | None:
        """### Load robots.txt, allowing confirmed absence and failing closed otherwise."""
        result: RobotsFile | None = None
        for attempt in range(self._max_retries + 1):
            result = self._read_file(robots_url)
            if result.status_code == 404:
                self._logger.warning("robots_absent_allow_all", extra={"url": robots_url})
                return None
            if result.status_code == 200 and result.error is None:
                text = result.text or ""
                lines = text.splitlines()
                directives = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
                valid = all(":" in line for line in directives)
                has_group = any(line.lower().startswith(("user-agent:", "sitemap:")) for line in directives)
                if directives and (not valid or not has_group):
                    self._logger.error("robots_malformed", extra={"url": robots_url})
                    return self._deny_all()
                parser = RobotFileParser()
                parser.parse(lines)
                return parser
            retryable = result.error is not None and result.status_code is None
            retryable = retryable or result.status_code in RETRYABLE_STATUS_CODES
            if retryable and attempt < self._max_retries:
                delay = retry_delay(
                    attempt + 1,
                    backoff_seconds=self._backoff_seconds,
                    retry_after_seconds=result.retry_after_seconds,
                )
                self._sleep(delay)
                continue
            break
        self._logger.error(
            "robots_unavailable_fail_closed",
            extra={
                "url": robots_url,
                "status": result.status_code if result else None,
                "error": result.error if result else None,
            },
        )
        return self._deny_all()

    @staticmethod
    def _deny_all() -> RobotFileParser:
        """### Build a parser that rejects every URL when policy is unavailable."""
        parser = RobotFileParser()
        parser.parse(["User-agent: *", "Disallow: /"])
        return parser
