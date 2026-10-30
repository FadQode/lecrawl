"""### Tests for the UAE source contract and its validators."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from lexcrawl.models.document import TEXT_BEGIN_MARKER, compute_content_hash, ensure_text_marker
from lexcrawl.validation.uae import (
    PRIMARY_LANGUAGE,
    SUPPORTED_LANGUAGES,
    UAEArticle,
    UAEArticleLocalization,
    article_key_from_url,
    metadata_document,
    to_canonical,
    translated_article_url,
    validate_article,
    validate_listing_entry,
    validate_localization,
)

HOST = "listing.example.test"
AR_URL = f"https://{HOST}/ar/news/sample-article"
EN_URL = f"https://{HOST}/en/news/sample-article"
KEY = "sample-article"


def localization(
    *,
    language: str = "ar",
    url: str = AR_URL,
    title: str = "عنوان الخبر",
    status: str = "pending",
    text: str | None = None,
    content_hash: str | None = None,
    published_at: date | None = date(2026, 1, 8),
    image_url: str | None = f"https://{HOST}/storage/post/large/pic.png",
) -> UAEArticleLocalization:
    """### Build a localization payload for the contract tests."""
    if status == "complete":
        text = ensure_text_marker(text or "collected body")
        content_hash = content_hash or compute_content_hash(text)
    return UAEArticleLocalization.model_validate(
        {
            "language": language,
            "article_url": url,
            "listing_url": f"https://{HOST}/ar/news",
            "title": title,
            "published_at": published_at,
            "summary": None,
            "image_url": image_url,
            "text": text,
            "content_hash": content_hash,
            "extraction_status": status,
        }
    )


def build_article(
    *, primary: UAEArticleLocalization | None = None, **translations: UAEArticleLocalization
) -> UAEArticle:
    """### Assemble an article from the supplied localizations."""
    return UAEArticle(article_key=KEY, primary=primary or localization(), translations=dict(translations))


def test_arabic_only_pending_article_is_reported_as_incomplete() -> None:
    """### A pending article builds fine but is reported until content is collected."""
    article = build_article()
    assert article.source == "uae_legislation_news"
    assert article.primary_language == PRIMARY_LANGUAGE
    assert article.article_key == KEY
    assert article.completed_localizations == ()
    codes = {issue.code for issue in validate_article(article, expected_host=HOST).errors}
    assert codes == {"no_completed_localization"}


def test_bilingual_complete_article_is_valid() -> None:
    """### Both languages can complete with independent text and hashes."""
    arabic = localization(status="complete")
    english = localization(
        language="en",
        url=EN_URL,
        title="Sample English title",
        status="complete",
        text=ensure_text_marker("english body"),
    )
    article = build_article(primary=arabic, en=english)

    report = validate_article(article, expected_host=HOST)
    assert report.ok is True
    assert {item.language for item in article.completed_localizations} == {"ar", "en"}
    assert arabic.content_hash != english.content_hash


def test_missing_translation_is_represented_not_fabricated() -> None:
    """### A blocked English localization keeps its status and no text."""
    english = localization(language="en", url=EN_URL, status="access_blocked", text=None)
    article = build_article(primary=localization(status="complete"), en=english)
    assert article.translations["en"].extraction_status == "access_blocked"
    assert article.translations["en"].text is None
    assert validate_article(article, expected_host=HOST).ok is True


def test_article_requires_identity() -> None:
    """### A blank article key is rejected."""
    with pytest.raises(ValidationError):
        UAEArticle(article_key="   ", primary=localization())


def test_primary_must_be_arabic() -> None:
    """### The primary localization cannot be a translation."""
    with pytest.raises(ValidationError):
        UAEArticle(article_key=KEY, primary=localization(language="en", url=EN_URL))


def test_unsupported_language_is_rejected() -> None:
    """### Only the supported languages are accepted."""
    with pytest.raises(ValidationError):
        localization(language="fr")
    assert SUPPORTED_LANGUAGES == ("ar", "en")


def test_translation_key_must_match_its_language() -> None:
    """### A translation declared under the wrong key is rejected."""
    article = build_article()
    with pytest.raises(ValidationError):
        UAEArticle(
            article_key=KEY, primary=article.primary, translations={"fr": localization(language="en", url=EN_URL)}
        )
    with pytest.raises(ValidationError):
        UAEArticle(
            article_key=KEY,
            primary=article.primary,
            translations={"en": localization(language="en", url=EN_URL), "ar": localization()},
        )


def test_invalid_status_is_rejected() -> None:
    """### Unknown extraction statuses fail validation."""
    with pytest.raises(ValidationError):
        localization(status="done")


def test_empty_title_and_url_are_rejected() -> None:
    """### Blank identity fields cannot enter the contract."""
    with pytest.raises(ValidationError):
        localization(title="  ")
    with pytest.raises(ValidationError):
        localization(url="")


def test_complete_localization_requires_content() -> None:
    """### Completing without marked text and hash is rejected by the model."""
    with pytest.raises(ValidationError):
        UAEArticleLocalization.model_validate(
            {
                "language": "ar",
                "article_url": AR_URL,
                "title": "عنوان",
                "extraction_status": "complete",
            }
        )
    text = "body without marker"
    with pytest.raises(ValidationError):
        UAEArticleLocalization.model_validate(
            {
                "language": "ar",
                "article_url": AR_URL,
                "title": "عنوان",
                "text": text,
                "content_hash": compute_content_hash(text),
                "extraction_status": "complete",
            }
        )


def test_complete_localization_rejects_a_wrong_hash() -> None:
    """### A hash that does not match the text is rejected."""
    text = ensure_text_marker("body")
    with pytest.raises(ValidationError):
        UAEArticleLocalization.model_validate(
            {
                "language": "ar",
                "article_url": AR_URL,
                "title": "عنوان",
                "text": text,
                "content_hash": "0" * 64,
                "extraction_status": "complete",
            }
        )


def test_localization_validation_reports_url_scope_errors() -> None:
    """### Off-host URLs and mismatched slugs are reported."""
    foreign = localization(url="https://example.com/ar/news/sample-article")
    codes = {issue.code for issue in validate_localization(foreign, expected_host=HOST).errors}
    assert "url_out_of_scope" in codes


def test_article_validation_reports_a_slug_mismatch() -> None:
    """### A translation whose slug differs from the article key is reported."""
    article = build_article(
        primary=localization(status="complete"),
        en=localization(language="en", url=f"https://{HOST}/en/news/other-article", status="access_blocked"),
    )
    fields = {issue.field for issue in validate_article(article, expected_host=HOST).errors}
    assert "translations.en.article_url" in fields


def test_differing_dates_and_images_warn() -> None:
    """### Metadata differences are preserved and reported as warnings."""
    arabic = localization(status="complete")
    english = localization(
        language="en",
        url=EN_URL,
        status="complete",
        text=ensure_text_marker("english body"),
        published_at=date(2026, 1, 9),
        image_url=f"https://{HOST}/storage/post/large/other.png",
    )
    report = validate_article(build_article(primary=arabic, en=english), expected_host=HOST)
    codes = {issue.code for issue in report.warnings}
    assert codes == {"date_mismatch", "image_mismatch"}
    assert english.published_at == date(2026, 1, 9)


def test_article_with_no_completed_localization_is_reported() -> None:
    """### An article with no text anywhere is not silently accepted."""
    report = validate_article(build_article(), expected_host=HOST)
    assert "no_completed_localization" in {issue.code for issue in report.errors}


def test_listing_entry_validation_checks_language_prefix() -> None:
    """### A record whose URL language disagrees with the record is rejected."""
    report = validate_listing_entry(
        title="عنوان",
        article_url=EN_URL,
        listing_url=f"https://{HOST}/ar/news",
        language="ar",
        expected_host=HOST,
    )
    assert "language_prefix_mismatch" in {issue.code for issue in report.errors}

    ok = validate_listing_entry(
        title="عنوان",
        article_url=AR_URL,
        listing_url=f"https://{HOST}/ar/news",
        language="ar",
        expected_host=HOST,
    )
    assert ok.ok is True


def test_url_helpers_swap_language_and_derive_keys() -> None:
    """### The language prefix swaps while the slug and article key stay stable."""
    assert translated_article_url(AR_URL, target_language="en") == EN_URL
    assert translated_article_url(EN_URL, target_language="ar") == AR_URL
    assert article_key_from_url(AR_URL) == article_key_from_url(EN_URL) == KEY
    assert article_key_from_url(f"{AR_URL}/") == KEY


def test_to_canonical_prefers_arabic() -> None:
    """### The canonical document keeps Arabic as the primary body."""
    arabic = localization(status="complete")
    english = localization(language="en", url=EN_URL, status="complete", text=ensure_text_marker("english body"))
    document = to_canonical(build_article(primary=arabic, en=english))

    assert document is not None
    assert document.language == "ar"
    assert document.source_url == AR_URL
    assert document.text == arabic.text
    assert [translation.language for translation in document.translations] == ["en"]
    assert document.translations[0].source_url == EN_URL
    assert document.translations[0].text.startswith(TEXT_BEGIN_MARKER)


def test_to_canonical_falls_back_to_english() -> None:
    """### When Arabic is blocked, the English localization is preserved."""
    arabic = localization(status="access_blocked", text=None)
    english = localization(language="en", url=EN_URL, status="complete", text=ensure_text_marker("english body"))
    document = to_canonical(build_article(primary=arabic, en=english))

    assert document is not None
    assert document.language == "en"
    assert document.source_url == EN_URL
    assert document.text is not None and document.text.startswith(TEXT_BEGIN_MARKER)
    assert document.translations == ()


def test_to_canonical_returns_none_without_content() -> None:
    """### No completed localization yields no canonical document."""
    assert to_canonical(build_article()) is None


def test_metadata_document_keeps_discovery_fields() -> None:
    """### A metadata-only document carries URLs and titles but no text."""
    article = build_article(primary=localization(status="access_blocked", text=None))
    document = metadata_document(article)
    assert document.text is None
    assert document.content_hash is None
    assert document.source_url == AR_URL
    assert document.published_at == date(2026, 1, 8)
    assert document.language == "ar"
