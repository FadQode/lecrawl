"""### Integration tests for the bilingual, validated UAE pipeline."""

from __future__ import annotations

from lexcrawl.actors.uae_legislation import UaeLegislationActor
from lexcrawl.crawling.base import AccessBlockedError, FetchError
from lexcrawl.models.document import TEXT_BEGIN_MARKER

from ..conftest import StubFetcher
from ..samples import (
    ARTICLE_PREFIX,
    EN_ARTICLE_PREFIX,
    EN_LISTING_URL,
    EN_PAGE_TWO_URL,
    EN_TITLE,
    LISTING_URL,
    PAGE_TWO_URL,
    detail_html,
    detail_html_en,
    listing_html,
)

AR_ARTICLE = f"{ARTICLE_PREFIX}article-1"
EN_ARTICLE = f"{EN_ARTICLE_PREFIX}article-1"
BLOCKED = AccessBlockedError(
    "source refused the request (Attention Required! | Cloudflare)",
    url=EN_ARTICLE,
    status_code=403,
    reason="Attention Required! | Cloudflare",
)


def build_actor(
    *,
    bilingual: bool = True,
    secondary_listing_url: str = "",
    max_listing_pages: int = 25,
) -> UaeLegislationActor:
    """### Build the actor with the sample listings and the requested mode."""
    return UaeLegislationActor(
        listing_url=LISTING_URL,
        max_listing_pages=max_listing_pages,
        bilingual=bilingual,
        secondary_listing_url=secondary_listing_url,
    )


def build_fetcher(
    *,
    cards: int = 2,
    failures: dict[str, Exception] | None = None,
    english_detail: bytes | None = None,
    secondary_listing: bool = False,
) -> StubFetcher:
    """### Serve both listings and both language variants of each article."""
    pages: dict[str, bytes] = {
        LISTING_URL: listing_html(cards=cards, next_page=None),
        PAGE_TWO_URL: listing_html(cards=cards, next_page=None),
    }
    if secondary_listing:
        pages[EN_LISTING_URL] = listing_html(cards=cards, next_page=None, language="en")
        pages[EN_PAGE_TWO_URL] = listing_html(cards=cards, next_page=None, language="en")
    for index in range(1, cards + 1):
        pages[f"{ARTICLE_PREFIX}article-{index}"] = detail_html()
        pages[f"{EN_ARTICLE_PREFIX}article-{index}"] = (
            english_detail if english_detail is not None else detail_html_en()
        )
    return StubFetcher(pages, failures=failures)


def test_both_languages_complete() -> None:
    """### A bilingual article stores both localizations with their own hashes."""
    result = build_actor().run(build_fetcher(cards=1))
    document = result.documents[0]

    assert result.status == "success"
    assert result.detail_pages_attempted == 2
    assert result.detail_pages_succeeded == 2
    assert document.language == "ar"
    assert document.text is not None and document.text.startswith(TEXT_BEGIN_MARKER)
    assert len(document.translations) == 1
    english = document.translations[0]
    assert english.language == "en"
    assert english.source_url == EN_ARTICLE
    assert english.title == EN_TITLE
    assert english.text.startswith(TEXT_BEGIN_MARKER)
    assert english.content_hash != document.content_hash


def test_english_blocked_keeps_arabic() -> None:
    """### A blocked translation preserves the Arabic document and states the block."""
    result = build_actor().run(build_fetcher(cards=1, failures={EN_ARTICLE: BLOCKED}))

    assert result.status == "partial_failure"
    assert result.detail_pages_attempted == 2
    assert result.detail_pages_succeeded == 1
    assert result.detail_pages_failed == 1
    document = result.documents[0]
    assert document.language == "ar"
    assert document.text is not None
    assert document.translations == ()
    assert any("access_blocked" not in message and "refused the request" in message for message in result.errors)


def test_arabic_blocked_keeps_english() -> None:
    """### A blocked primary does not discard the English localization."""
    result = build_actor().run(build_fetcher(cards=1, failures={AR_ARTICLE: BLOCKED}))
    document = result.documents[0]

    assert result.status == "partial_failure"
    assert document.language == "en"
    assert document.source_url == EN_ARTICLE
    assert document.text is not None and document.text.startswith(TEXT_BEGIN_MARKER)
    assert document.translations == ()
    assert document.content_hash is not None


def test_malformed_english_keeps_arabic_and_reports_the_error() -> None:
    """### An unparsable translation is reported while Arabic is preserved."""
    result = build_actor().run(build_fetcher(cards=1, english_detail=detail_html_en(with_container=False)))
    document = result.documents[0]

    assert document.language == "ar"
    assert document.text is not None
    assert result.records_missing_required_fields == 1
    assert any("article parse failed" in message for message in result.errors)


def test_both_languages_blocked_stores_metadata_only() -> None:
    """### When both variants are blocked only discovery metadata is stored."""
    failures = {AR_ARTICLE: BLOCKED, EN_ARTICLE: BLOCKED}
    result = build_actor().run(build_fetcher(cards=1, failures=failures))
    document = result.documents[0]

    assert result.status == "partial_failure"
    assert result.detail_pages_attempted == 2
    assert result.detail_pages_failed == 2
    assert result.records_without_text == 1
    assert document.text is None
    assert document.content_hash is None
    assert document.source_url == AR_ARTICLE
    assert document.title


def test_missing_translation_page_is_marked_unavailable() -> None:
    """### A 404 on the translation is recorded as unavailable, not blocked."""
    not_found = FetchError("unexpected status 404", url=EN_ARTICLE, status_code=404)
    result = build_actor().run(build_fetcher(cards=1, failures={EN_ARTICLE: not_found}))
    document = result.documents[0]

    assert document.language == "ar"
    assert document.translations == ()
    assert result.detail_pages_failed == 1


def test_both_listings_merge_into_one_article_each() -> None:
    """### Discovering both listings contributes one record per article, not two."""
    fetcher = build_fetcher(cards=2, secondary_listing=True)
    result = build_actor(secondary_listing_url=EN_LISTING_URL).run(fetcher)

    assert result.articles_discovered == 2
    assert len(result.documents) == 2
    assert all(len(document.translations) == 1 for document in result.documents)
    assert result.listing_pages_attempted >= 2


def test_a_listing_repeated_as_the_secondary_language_is_rejected() -> None:
    """### Walking the same listing under another language is rejected, not duplicated."""
    fetcher = build_fetcher(cards=2, secondary_listing=True)
    result = build_actor(secondary_listing_url=LISTING_URL).run(fetcher)

    assert result.articles_discovered == 2
    assert len(result.documents) == 2
    assert any("language_prefix_mismatch" in message for message in result.errors)


def test_repeated_runs_are_idempotent() -> None:
    """### Running twice yields the same canonical rows without duplicates."""
    first = build_actor().run(build_fetcher(cards=2))
    second = build_actor().run(build_fetcher(cards=2))

    first_urls = [document.source_url for document in first.documents]
    second_urls = [document.source_url for document in second.documents]
    assert first_urls == second_urls
    assert len(set(first_urls)) == len(first_urls)
    assert [document.content_hash for document in first.documents] == [
        document.content_hash for document in second.documents
    ]


def test_listing_card_with_a_foreign_language_prefix_is_skipped() -> None:
    """### A card whose URL belongs to another language listing is not enriched."""
    listing = (
        f'<div class="item_"><a class="link_" href="{EN_ARTICLE}"><div class="title_">عنوان</div></a></div>'
    ).encode()
    fetcher = StubFetcher({LISTING_URL: listing, EN_ARTICLE: detail_html_en()})
    result = build_actor(bilingual=False).run(fetcher)

    assert result.articles_discovered == 0
    assert result.documents == []
    assert any("skipped 1 malformed card" in message for message in result.errors)
    assert fetcher.call_count(EN_ARTICLE) == 0


def test_arabic_only_mode_keeps_the_previous_behaviour() -> None:
    """### Without bilingual mode only the primary localization is fetched."""
    result = build_actor(bilingual=False).run(build_fetcher(cards=2))

    assert result.detail_pages_attempted == 2
    assert len(result.documents) == 2
    assert all(document.language == "ar" for document in result.documents)
    assert all(document.translations == () for document in result.documents)
    assert result.status == "success"
