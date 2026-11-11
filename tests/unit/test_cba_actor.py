"""### End-to-end actor test for the CBA source through a mocked network."""

from __future__ import annotations

import httpx
import respx

from lexcrawl.actors.cba import CbaNewsReleasesActor
from lexcrawl.crawling.http import HttpFetcher
from lexcrawl.models.document import TEXT_BEGIN_MARKER
from samples import (
    RELEASES_API_URL,
    RELEASES_ARTICLE_PATH,
    RELEASES_OTHER_TYPE_ID,
    RELEASES_SITE_URL,
    RELEASES_TYPE_ID,
    cba_article_html,
    cba_listing_page,
    cba_listing_record,
)


def _actor() -> CbaNewsReleasesActor:
    """### Build a CBA actor with deterministic bounds."""
    return CbaNewsReleasesActor(
        api_url=RELEASES_API_URL,
        site_url=RELEASES_SITE_URL,
        api_language="en-us",
        type_guid=RELEASES_TYPE_ID,
        article_path=RELEASES_ARTICLE_PATH,
        page_size=50,
        max_pages=2,
    )


def _fetcher() -> HttpFetcher:
    """### Build a fetcher with pacing disabled for fast tests."""
    return HttpFetcher(user_agent="LexCrawl-Test/0.1", request_delay_seconds=0.0, sleep=lambda _seconds: None)


@respx.mock
def test_actor_collects_releases_and_filters_others() -> None:
    """### Only news releases are enriched; duplicates and other types are counted."""
    respx.post(RELEASES_API_URL).mock(
        return_value=httpx.Response(
            200,
            content=cba_listing_page(
                [
                    cba_listing_record(slug="release-a"),
                    cba_listing_record(slug="release-b"),
                    cba_listing_record(slug="release-a"),  # duplicate
                    cba_listing_record(slug="opinion-c", type_ids=(RELEASES_OTHER_TYPE_ID,)),
                ]
            ),
        )
    )
    respx.get(f"{RELEASES_SITE_URL}/article/release-a").mock(
        return_value=httpx.Response(200, content=cba_article_html())
    )
    respx.get(f"{RELEASES_SITE_URL}/article/release-b").mock(
        return_value=httpx.Response(200, content=cba_article_html(title="Release B"))
    )

    with _fetcher() as fetcher:
        result = _actor().run(fetcher)

    assert result.pagination_complete is True
    assert result.articles_discovered == 2
    assert result.article_urls_deduplicated == 1
    assert result.records_filtered_out == 1
    assert result.status == "success"
    assert len(result.documents) == 2
    collected = {document.source_url.rsplit("/", 1)[-1]: document for document in result.documents}
    assert collected["release-a"].text is not None
    assert collected["release-a"].text.startswith(TEXT_BEGIN_MARKER)
    assert collected["release-b"].title == "Release B"


@respx.mock
def test_actor_keeps_metadata_when_detail_fetch_fails() -> None:
    """### A failed detail fetch yields a text-less metadata record, not a lost record."""
    respx.post(RELEASES_API_URL).mock(
        return_value=httpx.Response(200, content=cba_listing_page([cba_listing_record()]))
    )
    respx.get(f"{RELEASES_SITE_URL}/article/sample-release").mock(return_value=httpx.Response(404, text="missing"))

    with _fetcher() as fetcher:
        result = _actor().run(fetcher)

    assert len(result.documents) == 1
    document = result.documents[0]
    assert document.text is None
    assert document.extraction_status == "unavailable"
    assert result.records_without_text == 1
    assert result.status == "partial_failure"
