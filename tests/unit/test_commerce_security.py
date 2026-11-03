"""### Credential hygiene tests for the Commerce Firecrawl path."""

from __future__ import annotations

from lexcrawl.actors.commerce import CommerceReportsActor
from lexcrawl.crawling.base import FetchedPage, FetchError
from lexcrawl.crawling.firecrawl import FirecrawlError, ScrapedPage

LISTING = "https://www.commerce.gov/data-and-reports/reports"
SECRET = "sensitive-key-value"


class FailingFetcher:
    """### Fail every listing request with a diagnostic."""

    engine_name = "http"

    def fetch(self, url: str) -> FetchedPage:
        """### Fail the request with a message that echoes the URL."""
        raise FetchError(f"request failed at {url}", url=url)

    def fetch_binary(self, url: str, *, max_bytes: int | None = None) -> FetchedPage:
        """### Reject binary requests in this listing-only test fetcher."""
        raise FetchError("unexpected binary request", url=url)

    def post_json(self, url: str, *, payload: str) -> FetchedPage:
        """### Reject JSON posts in this GET-only test fetcher."""
        raise FetchError("unexpected post", url=url)

    def close(self) -> None:
        """### Release no resources."""
        return None


class FailingScraper:
    """### Fail every scrape with a diagnostic that echoes the URL."""

    def __init__(self) -> None:
        """### Keep the requested URLs for assertions."""
        self.calls: list[str] = []

    def scrape(self, url: str, *, cache: bool = False) -> ScrapedPage:
        """### Raise a provider error for every URL."""
        self.calls.append(url)
        raise FirecrawlError(f"provider rejected {url}", url=url, status_code=403)


def build_actor(scraper: FailingScraper | None, *, known: set[str] | None = None, bootstrap: bool = False):  # type: ignore[no-untyped-def]
    """### Build the actor with an explicit identity baseline."""
    return CommerceReportsActor(
        listing_url=LISTING,
        firecrawl=scraper,
        known_identities=(lambda: set(known or ())) if known is not None else None,
        bootstrap=bootstrap,
    )


def test_preflight_errors_carry_no_credentials() -> None:
    """### Change-detection failures never leak the configured secret."""
    actor = build_actor(FailingScraper(), known=set())
    result = actor.run(FailingFetcher())

    assert result.status == "failed"
    assert all(SECRET not in message for message in result.errors)
    assert any("change detection fetch failed" in message for message in result.errors)


def test_firecrawl_failures_are_reported_without_the_key() -> None:
    """### Provider errors surface the URL but never the API key."""
    actor = build_actor(FailingScraper(), known=set())
    result = actor.run(FailingFetcher())
    assert all("Bearer" not in message for message in result.errors)
    assert all(SECRET not in message for message in result.errors)


def test_actor_does_not_echo_secrets_from_provider_messages() -> None:
    """### Bootstrap failures keep only the redacted URL in diagnostics."""
    scraper = FailingScraper()
    result = build_actor(scraper, known=set(), bootstrap=True).run(FailingFetcher())

    assert result.check_outcome == "new_data"
    assert all(SECRET not in message for message in result.errors)
    assert all("api_key=" not in message for message in result.errors)
