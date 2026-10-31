"""### Credential redaction tests for the Commerce ingestion path."""

from lexcrawl.actors.commerce import CommerceReportsActor
from lexcrawl.crawling.base import FetchedPage, FetchError


class FailingFetcher:
    """### Raise a fetch error containing the requested URL for regression coverage."""

    engine_name = "http"

    def fetch(self, url: str) -> FetchedPage:
        """### Fail the request while echoing the sensitive URL into its diagnostic."""
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


def test_commerce_failure_diagnostics_do_not_expose_api_key() -> None:
    """### API keys stay out of actor errors even when the fetcher echoes its URL."""
    actor = CommerceReportsActor(
        api_url="https://api.example.test/api/news",
        api_key="sensitive-key-value",
        article_host="www.example.test",
        article_paths=("/reports/",),
        news_types=("Reports",),
        page_size=1,
        max_pages=1,
    )

    result = actor.run(FailingFetcher())

    assert result.status == "failed"
    assert all("sensitive-key-value" not in message for message in result.errors)
    diagnostics = [message for message in result.errors if "api_key=" in message]
    assert diagnostics
    assert all("api_key=%5BREDACTED%5D" in message for message in diagnostics)
