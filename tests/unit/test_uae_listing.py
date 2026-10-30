"""### Listing parser tests for the UAE Legislation news source."""

from __future__ import annotations

from datetime import date

from lexcrawl.actors.uae_legislation import is_article_url, parse_listing, parse_published_date

from ..samples import ARTICLE_PREFIX, LISTING_HOST, LISTING_URL, PAGE_TWO_URL, listing_html

ARTICLE_ONE_URL = f"{ARTICLE_PREFIX}article-1"


def test_listing_sample_yields_exactly_twenty_articles() -> None:
    """### The 20-card listing sample yields exactly 20 records."""
    page = parse_listing(listing_html(), LISTING_URL)
    assert len(page.stubs) == 20
    assert page.skipped_cards == 0
    assert page.duplicate_urls == 0
    assert len({stub.url for stub in page.stubs}) == 20


def test_listing_extracts_expected_field_values() -> None:
    """### A card exposes title, date, summary, URL, and image URL."""
    page = parse_listing(listing_html(), LISTING_URL)
    first = page.stubs[0]
    assert first.url == ARTICLE_ONE_URL
    assert first.title == "خبر رقم 1"
    assert first.published_at == date(2026, 1, 1)
    assert first.image_url == f"https://{LISTING_HOST}/storage/post/large/1.png"
    second = page.stubs[1]
    assert second.summary == "ملخص الخبر رقم 2"
    assert second.published_at == date(2026, 1, 2)


def test_listing_tolerates_a_card_without_summary() -> None:
    """### Absent optional summaries are reported as None, not as failures."""
    page = parse_listing(listing_html(), LISTING_URL)
    assert page.stubs[0].summary is None
    assert sum(1 for stub in page.stubs if stub.summary is not None) == 19


def test_listing_reports_pagination_target() -> None:
    """### The observed `?page=2` link is followed as the next page."""
    assert parse_listing(listing_html(), LISTING_URL).next_page_url == PAGE_TWO_URL


def test_listing_without_a_next_link_ends_the_walk() -> None:
    """### A page with no next control reports no continuation."""
    assert parse_listing(listing_html(cards=3, next_page=None), LISTING_URL).next_page_url is None


def test_listing_resolves_relative_urls_and_skips_malformed_cards() -> None:
    """### Relative URLs resolve against the page and unusable cards are skipped."""
    html = """
    <div class="item_">
      <a class="link_" href="/ar/news/relative-card">
        <div class="img_"><img src="../storage/post/large/image.png"/></div>
        <div class="dateBox"><span>Feb 04 2026</span></div>
        <div class="title_">خبر نسبي</div>
      </a>
    </div>
    <div class="item_">
      <a class="link_" href="https://example.com/ar/news/external"><div class="title_">خارجي</div></a>
    </div>
    <div class="item_"><a class="link_" href="/ar/news"><div class="title_">القائمة نفسها</div></a></div>
    <div class="item_"><a class="link_" href="/ar/news/no-title"><div class="title_"> </div></a></div>
    <div class="item_"><div class="title_">بدون رابط</div></div>
    """
    page = parse_listing(html, LISTING_URL)
    assert len(page.stubs) == 1
    stub = page.stubs[0]
    assert stub.url == f"https://{LISTING_HOST}/ar/news/relative-card"
    assert stub.published_at == date(2026, 2, 4)
    assert stub.image_url == f"https://{LISTING_HOST}/storage/post/large/image.png"
    assert stub.summary is None
    assert page.skipped_cards == 4


def test_listing_does_not_emit_duplicate_urls() -> None:
    """### A repeated card contributes one record and one duplicate counter."""
    page = parse_listing(listing_html(cards=2, duplicate_first=True), LISTING_URL)
    assert len(page.stubs) == 2
    assert page.duplicate_urls == 1


def test_listing_falls_back_to_the_picture_source_for_images() -> None:
    """### An `img` without `src` uses the `source` element inside `picture`."""
    html = """
    <div class="item_">
      <a class="link_" href="/ar/news/one">
        <div class="img_"><picture><source srcset="/storage/post/large/pic.png"/><img/></picture></div>
        <div class="title_">عنوان</div>
      </a>
    </div>
    """
    assert parse_listing(html, LISTING_URL).stubs[0].image_url == (f"https://{LISTING_HOST}/storage/post/large/pic.png")


def test_listing_ignores_pagination_links_without_a_target() -> None:
    """### A disabled next control ends the pagination walk."""
    html = """
    <div class="item_"><a class="link_" href="/ar/news/one"><div class="title_">عنوان</div></a></div>
    <div class="table_pagination"><ul><li><a class="page-link next_" href="#">next</a></li></ul></div>
    """
    assert parse_listing(html, LISTING_URL).next_page_url is None


def test_is_article_url_rejects_out_of_scope_urls() -> None:
    """### Only same-host `/ar/news/<slug>` URLs are considered in scope."""
    scope = {"host": LISTING_HOST, "path_prefix": "/ar/news/"}
    assert is_article_url(ARTICLE_ONE_URL, **scope)
    assert not is_article_url(LISTING_URL, **scope)
    assert not is_article_url("https://example.com/ar/news/external", **scope)
    assert not is_article_url(f"https://{LISTING_HOST}/en/news/external", **scope)
    assert not is_article_url(f"https://{LISTING_HOST}/storage/post/large/image.png", **scope)
    assert not is_article_url("javascript:void(0)", **scope)


def test_parse_published_date_handles_observed_and_invalid_values() -> None:
    """### Observed date formats parse and unusable values return None."""
    assert parse_published_date("Jan 08 2026") == date(2026, 1, 8)
    assert parse_published_date("Feb 04 2026") == date(2026, 2, 4)
    assert parse_published_date("2026-01-08") == date(2026, 1, 8)
    assert parse_published_date("08 Jan 2026") == date(2026, 1, 8)
    assert parse_published_date("") is None
    assert parse_published_date("   ") is None
    assert parse_published_date("not a date") is None
