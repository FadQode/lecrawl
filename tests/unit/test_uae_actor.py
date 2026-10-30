"""### Orchestration tests for the UAE Legislation actor without live network."""

from __future__ import annotations

from datetime import date

from lexcrawl.actors.base import ActorRunResult
from lexcrawl.actors.uae_legislation import UaeLegislationActor, parse_listing
from lexcrawl.crawling.base import AccessBlockedError, FetchError
from lexcrawl.models.document import TEXT_BEGIN_MARKER, CanonicalDocument, RawArticle

from ..conftest import StubFetcher
from ..samples import ARTICLE_PREFIX, LISTING_URL, PAGE_TWO_URL, detail_html, listing_html

BLOCKED = AccessBlockedError(
    "source refused the request (Attention Required! | Cloudflare)",
    url=LISTING_URL,
    status_code=403,
    reason="Attention Required! | Cloudflare",
)


def build_actor(*, max_listing_pages: int = 25, concurrency: int = 1) -> UaeLegislationActor:
    """### Build the actor against the sample listing."""
    return UaeLegislationActor(
        listing_url=LISTING_URL,
        max_listing_pages=max_listing_pages,
        concurrency=concurrency,
    )


def build_fetcher(
    *,
    listing: bytes | None = None,
    detail: bytes | None = None,
    failures: dict[str, Exception] | None = None,
    detail_overrides: dict[str, bytes] | None = None,
) -> StubFetcher:
    """### Serve the listing plus one canned article body for every article URL."""
    listing_body = listing if listing is not None else listing_html()
    detail_body = detail if detail is not None else detail_html()
    pages = {LISTING_URL: listing_body, PAGE_TWO_URL: listing_body}
    for stub in parse_listing(listing_body, LISTING_URL).stubs:
        pages[stub.url] = (detail_overrides or {}).get(stub.url, detail_body)
    return StubFetcher(pages, failures=failures)


def test_actor_collects_every_article_across_pages() -> None:
    """### Pagination is followed once, and all 20 articles become documents."""
    fetcher = build_fetcher()
    result = build_actor().run(fetcher)

    assert result.listing_pages_attempted == 2
    assert result.listing_pages_succeeded == 2
    assert result.listing_pages_failed == 0
    assert result.articles_discovered == 20
    assert result.article_urls_deduplicated == 20
    assert result.detail_pages_attempted == 20
    assert result.detail_pages_succeeded == 20
    assert result.detail_pages_failed == 0
    assert len(result.documents) == 20
    assert result.pagination_complete is True
    assert result.status == "success"
    assert all(document.text.startswith(TEXT_BEGIN_MARKER) for document in result.documents)


def test_actor_never_fetches_the_same_url_twice() -> None:
    """### Listing pages and article pages are each requested at most once."""
    fetcher = build_fetcher()
    build_actor().run(fetcher)

    assert fetcher.call_count(LISTING_URL) == 1
    assert fetcher.call_count(PAGE_TWO_URL) == 1
    assert len(fetcher.calls) == len(set(fetcher.calls))
    assert len([url for url in fetcher.calls if url.startswith(f"{LISTING_URL}/")]) == 20


def test_actor_reports_partial_failure_when_one_article_fails() -> None:
    """### A single article failure keeps the other articles and is reported."""
    broken_url = parse_listing(listing_html(), LISTING_URL).stubs[3].url
    fetcher = build_fetcher(failures={broken_url: FetchError("connection reset", url=broken_url)})
    result = build_actor().run(fetcher)

    assert result.detail_pages_attempted == 20
    assert result.detail_pages_failed == 1
    assert result.detail_pages_succeeded == 19
    assert len(result.documents) == 20
    assert result.records_without_text == 1
    assert result.status == "partial_failure"
    assert any("article fetch failed" in error for error in result.errors)

    blocked = next(document for document in result.documents if document.text is None)
    assert blocked.source_url == broken_url
    assert blocked.title
    assert blocked.published_at is not None


def test_actor_counts_articles_missing_required_fields() -> None:
    """### An article page without a body is counted and reported, not stored."""
    target = parse_listing(listing_html(), LISTING_URL).stubs[0].url
    fetcher = build_fetcher(detail_overrides={target: detail_html(paragraphs=())})
    result = build_actor().run(fetcher)

    assert result.records_missing_required_fields == 1
    assert result.detail_pages_failed == 1
    assert result.records_without_text == 1
    assert len(result.documents) == 20
    assert any("article parse failed" in error for error in result.errors)


def test_actor_fails_clearly_when_the_listing_is_blocked() -> None:
    """### A blocked listing fails the run with the protection reason recorded."""
    fetcher = build_fetcher(failures={LISTING_URL: BLOCKED})
    result = build_actor().run(fetcher)

    assert result.status == "failed"
    assert result.listing_pages_failed == 1
    assert result.articles_discovered == 0
    assert result.documents == []
    assert result.pagination_complete is False
    assert any("refused the request" in error for error in result.errors)


def test_actor_stops_at_the_configured_page_bound() -> None:
    """### The page bound is honoured and reported as incomplete pagination."""
    fetcher = build_fetcher()
    result = build_actor(max_listing_pages=1).run(fetcher)

    assert result.listing_pages_attempted == 1
    assert result.pagination_complete is False
    assert fetcher.call_count(PAGE_TWO_URL) == 0
    assert any("max_listing_pages" in error for error in result.errors)


def test_actor_keeps_every_listing_row_when_all_details_are_blocked() -> None:
    """### Metadata for all 20 articles survives a fully blocked detail crawl."""
    failures = {
        stub.url: AccessBlockedError(
            "source refused the request",
            url=stub.url,
            status_code=403,
            reason="Attention Required! | Cloudflare",
        )
        for stub in parse_listing(listing_html(), LISTING_URL).stubs
    }
    result = build_actor().run(build_fetcher(failures=failures))

    assert result.status == "partial_failure"
    assert len(result.documents) == 20
    assert result.detail_pages_succeeded == 0
    assert result.records_without_text == 20
    assert all(document.text is None for document in result.documents)
    assert all(document.source_url and document.title for document in result.documents)
    assert sum(1 for document in result.documents if document.published_at) == 20


def test_actor_fails_when_listing_has_no_articles() -> None:
    """### An empty listing is a failure, never a successful empty crawl."""
    fetcher = StubFetcher({LISTING_URL: b"<html><body>no cards here</body></html>"})
    result = build_actor().run(fetcher)

    assert result.status == "failed"
    assert result.articles_discovered == 0
    assert any("no article URLs discovered" in error for error in result.errors)


def test_actor_merges_listing_metadata_into_documents() -> None:
    """### Listing summaries and dates reach the canonical document."""
    result = build_actor().run(build_fetcher())

    assert sum(1 for document in result.documents if document.summary) == 19
    assert all(document.published_at == date(2026, 1, 8) for document in result.documents)


def test_actor_reports_skipped_malformed_cards() -> None:
    """### Cards that cannot be used are counted and surfaced as an error."""
    listing = (
        '<div class="item_"><a class="link_" href="/ar/news/one"><div class="title_">عنوان</div></a></div>'
        '<div class="item_"><div class="title_">بدون رابط</div></div>'
    ).encode()
    fetcher = StubFetcher({LISTING_URL: listing, f"{LISTING_URL}/one": detail_html()})
    result = build_actor().run(fetcher)

    assert result.articles_discovered == 1
    assert len(result.documents) == 1
    assert result.status == "partial_failure"
    assert any("skipped 1 malformed card" in error for error in result.errors)


def test_actor_inherits_listing_metadata_when_detail_omits_it() -> None:
    """### Dates and images fall back to the listing card when absent downstream."""
    fetcher = build_fetcher(detail=detail_html(date_text=None, image_url=None))
    result = build_actor().run(fetcher)

    assert all(document.published_at is not None for document in result.documents)
    assert all(document.image_url is not None for document in result.documents)
    assert result.documents[0].published_at == date(2026, 1, 1)


def test_actor_detects_a_pagination_loop() -> None:
    """### A listing cycle is detected and reported instead of looping forever."""

    def listing_page(next_url: str) -> bytes:
        """### Build a listing page whose next link points at a given URL."""
        return (
            '<div class="item_"><a class="link_" href="/ar/news/one"><div class="title_">عنوان</div></a></div>'
            f'<div class="table_pagination"><a class="page-link next_" href="{next_url}">next</a></div>'
        ).encode()

    fetcher = StubFetcher(
        {
            LISTING_URL: listing_page(PAGE_TWO_URL),
            PAGE_TWO_URL: listing_page(LISTING_URL),
            f"{LISTING_URL}/one": detail_html(),
        }
    )
    result = build_actor().run(fetcher)

    assert result.listing_pages_attempted == 2
    assert result.pagination_complete is False
    assert len(result.documents) == 1
    assert any("pagination loop detected" in error for error in result.errors)


def test_run_result_status_semantics() -> None:
    """### Run status distinguishes empty failure, partial success, and success."""
    document = CanonicalDocument.from_raw(
        RawArticle(
            source="s",
            url=f"{ARTICLE_PREFIX}one",
            title="عنوان",
            language="ar",
            paragraphs=["نص"],
        )
    )
    assert ActorRunResult(source="s").status == "failed"
    assert ActorRunResult(source="s", errors=["boom"]).status == "failed"
    assert ActorRunResult(source="s", documents=[document]).status == "success"
    assert ActorRunResult(source="s", documents=[document], errors=["boom"]).status == "partial_failure"
