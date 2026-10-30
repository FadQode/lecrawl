"""### robots.txt policy enforcement shared by every crawling strategy."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from lexcrawl.crawling.base import RobotsDisallowedError
from lexcrawl.logging import get_logger


@dataclass(frozen=True, slots=True)
class RobotsFile:
    """### Result of retrieving one host's robots.txt."""

    status_code: int | None = None
    text: str | None = None
    error: str | None = None


class RobotsPolicy:
    """### Cache and apply each host's published robots policy."""

    def __init__(self, read_file: Callable[[str], RobotsFile], *, user_agent: str) -> None:
        """### Store the retrieval strategy and the identity used for evaluation."""
        self._read_file = read_file
        self._user_agent = user_agent
        self._parsers: dict[str, RobotFileParser | None] = {}
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
        if host_key not in self._parsers:
            self._parsers[host_key] = self._load_parser(f"{host_key}/robots.txt")
        return self._parsers[host_key]

    def _load_parser(self, robots_url: str) -> RobotFileParser | None:
        """### Load one robots file, treating an unusable file as allow-all."""
        result = self._read_file(robots_url)
        if result.error is not None:
            self._logger.warning("robots_unreachable", extra={"url": robots_url, "error": result.error})
            return None
        if result.status_code != 200 or result.text is None:
            self._logger.warning("robots_unavailable", extra={"url": robots_url, "status": result.status_code})
            return None
        parser = RobotFileParser()
        parser.parse(result.text.splitlines())
        return parser
