"""### Unit tests for the source parsers (CBA, UAE, Commerce)."""

from __future__ import annotations

from datetime import date

import pytest

from lexcrawl.actors.cba import (
    CbaApiError,
    article_url,
    canonical_article_url,
    is_news_release,
    parse_article,
    parse_listing,
    parse_published_at,
)
from lexcrawl.actors.commerce import (
    CommerceExtractionError,
    InsufficientReportContent,
    build_page_url,
    filename_from_url,
    is_report_url,
    parse_listing_date,
    resource_kind,
)
from lexcrawl.actors.commerce import (
    parse_detail as commerce_parse_detail,
)
from lexcrawl.actors.commerce import (
    parse_listing as commerce_parse_listing,
)
from lexcrawl.actors.uae_legislation import (
    InsufficientArticleContent,
    UaeExtractionError,
    article_scope,
    is_article_url,
    parse_published_date,
)
from lexcrawl.actors.uae_legislation import (
    parse_detail as uae_parse_detail,
)
from lexcrawl.actors.uae_legislation import (
    parse_listing as uae_parse_listing,
)
from lexcrawl.models.document import ArticleContentError
from samples import (
    COMMERCE_HOST,
    COMMERCE_LISTING_PATH,
    COMMERCE_LISTING_URL,
    RELEASES_API_URL,
    RELEASES_ARTICLE_PATH,
    RELEASES_OTHER_TYPE_ID,
    RELEASES_SITE_URL,
    RELEASES_TYPE_ID,
    UAE_LISTING_URL,
    cba_article_html,
    cba_listing_page,
    cba_listing_record,
    commerce_detail_html,
    commerce_listing_html,
    commerce_row,
    uae_detail_html,
    uae_listing_html,
)


def cba_page(records: list[dict[str, object]]):  # type: ignore[no-untyped-def]
    """### Parse a CBA listing from sample records."""
    return parse_listing(
        cba_listing_page(records),
        api_url=RELEASES_API_URL,
        site_url=RELEASES_SITE_URL,
        article_path=RELEASES_ARTICLE_PATH,
    )


def test_cba_listing_and_type_filter() -> None:
    """### Listing records parse; type filtering distinguishes releases."""
    page = cba_page([cba_listing_record(), cba_listing_record(slug="b", type_ids=(RELEASES_OTHER_TYPE_ID,))])
    assert len(page.records) == 2
    release = page.records[0]
    assert is_news_release(release, RELEASES_TYPE_ID)
    assert not is_news_release(page.records[1], RELEASES_TYPE_ID)
    assert release.url == article_url(release.slug, site_url=RELEASES_SITE_URL, article_path=RELEASES_ARTICLE_PATH)


def test_cba_listing_rejects_non_json() -> None:
    """### A non-JSON body raises a typed error."""
    with pytest.raises(CbaApiError):
        parse_listing(
            b"<html>", api_url=RELEASES_API_URL, site_url=RELEASES_SITE_URL, article_path=RELEASES_ARTICLE_PATH
        )


def test_cba_article_parse() -> None:
    """### Article markup maps to the expected fields."""
    article = parse_article(cba_article_html(), "https://releases.example.test/article/x")
    assert article.title.startswith("Sample release")
    assert str(article.published_at) == "2026-09-25"
    assert article.topics == ("#Mortgages",)
    assert article.text.startswith("First section heading")


def test_cba_article_missing_title() -> None:
    """### A page without a title fails as a content error."""
    with pytest.raises(ArticleContentError):
        parse_article(cba_article_html(with_title=False), "https://releases.example.test/article/x")


def test_cba_helpers() -> None:
    """### Date parsing and URL canonicalization."""
    assert parse_published_at("2026-09-25T00:00:00") == date(2026, 9, 25)
    assert parse_published_at(None) is None
    assert canonical_article_url("https://x.test/a/") == "https://x.test/a"


def test_uae_listing_and_detail() -> None:
    """### UAE cards and articles parse, including Arabic text."""
    page = uae_parse_listing(uae_listing_html(cards=3), UAE_LISTING_URL)
    assert len(page.stubs) == 3
    assert page.next_page_url is not None
    article = uae_parse_detail(uae_detail_html(), url=f"{UAE_LISTING_URL}/article-1", min_body_words=3)
    assert article.title.startswith("إعلان")
    assert article.text.startswith("أعلنت")
    assert str(article.published_at) == "2026-01-08"


def test_uae_thin_and_missing() -> None:
    """### Thin bodies are flagged; missing title/body raise."""
    with pytest.raises(InsufficientArticleContent):
        uae_parse_detail(uae_detail_html(paragraphs=("قصير.",)), url="https://x.test/a", min_body_words=30)
    with pytest.raises(UaeExtractionError, match="no article title"):
        uae_parse_detail(uae_detail_html(with_title=False), url="https://x.test/a", min_body_words=3)
    with pytest.raises(UaeExtractionError, match="body is empty"):
        uae_parse_detail(uae_detail_html(with_body=False), url="https://x.test/a", min_body_words=3)


def test_uae_scope_and_dates() -> None:
    """### Scope derivation and date formats."""
    host, prefix = article_scope(UAE_LISTING_URL)
    assert is_article_url(f"{UAE_LISTING_URL}/article-1", host=host, path_prefix=prefix)
    assert str(parse_published_date("Jan 08 2026")) == "2026-01-08"
    assert parse_published_date("nope") is None


def _commerce_page(rows: list[tuple[str, str, str, object]], *, total: int | None = None):  # type: ignore[no-untyped-def]
    """### Parse a Commerce listing from sample rows."""
    return commerce_parse_listing(
        commerce_listing_html(rows, total=total),
        page_url=build_page_url(COMMERCE_LISTING_URL, 0),
        page_number=0,
        host=COMMERCE_HOST,
        path_prefixes=(f"{COMMERCE_LISTING_PATH}/",),
    )


def test_commerce_listing_and_detail() -> None:
    """### Listing rows (with sort control) and detail pages parse."""
    page = _commerce_page([commerce_row(), commerce_row(slug="b")], total=112)
    assert page is not None
    assert page.total_reports == 112
    entry = page.entries[0]
    assert entry.published_date_raw == "January 2025"
    assert entry.published_month == 1 and entry.published_year == 2025
    assert entry.resources[0].kind == "pdf"
    article = commerce_parse_detail(commerce_detail_html(), url="https://x.test/r", min_body_words=3)
    assert article.title.startswith("Sample Commerce")
    assert article.text.startswith("The Department")
    assert len(article.resources) == 1


def test_commerce_thin_and_missing() -> None:
    """### Thin bodies are flagged; missing article/title raise."""
    with pytest.raises(InsufficientReportContent):
        commerce_parse_detail(commerce_detail_html(paragraphs=("Short.",)), url="https://x.test/r", min_body_words=30)
    with pytest.raises(CommerceExtractionError, match="no article container"):
        commerce_parse_detail(commerce_detail_html(with_article=False), url="https://x.test/r", min_body_words=3)
    with pytest.raises(CommerceExtractionError, match="no report title"):
        commerce_parse_detail(commerce_detail_html(with_title=False), url="https://x.test/r", min_body_words=3)


def test_commerce_helpers() -> None:
    """### Kind/filename/scope/date helpers."""
    assert resource_kind("https://x.test/a/report.PDF") == "pdf"
    assert resource_kind("https://x.test/a/data.xlsx") == "excel"
    assert filename_from_url("https://x.test/a/sample%20report.pdf") == "sample report.pdf"
    host = COMMERCE_HOST
    prefixes = (f"{COMMERCE_LISTING_PATH}/",)
    assert is_report_url(f"{COMMERCE_LISTING_URL}/2025/01/x", host=host, path_prefixes=prefixes)
    assert not is_report_url("https://other.test/x", host=host, path_prefixes=prefixes)
    assert parse_listing_date("January 2025").month == 1
    assert parse_listing_date("nope").month is None
