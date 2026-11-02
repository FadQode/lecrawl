"""### UAE Legislation news source contract and validation rules."""

from __future__ import annotations

from datetime import date
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lexcrawl.models.document import (
    TEXT_BEGIN_MARKER,
    CanonicalDocument,
    DocumentTranslation,
    compute_content_hash,
)
from lexcrawl.validation.base import ValidationIssue, ValidationReport, host_issue, http_url_issue, text_issues

SourceName = Literal["uae_legislation_news"]

SOURCE_NAME: SourceName = "uae_legislation_news"
PRIMARY_LANGUAGE: Language = "ar"
SUPPORTED_LANGUAGES = ("ar", "en")

Language = Literal["ar", "en"]
ExtractionStatus = Literal["pending", "complete", "access_blocked", "failed", "unavailable"]


class UAEArticleLocalization(BaseModel):
    """### One language variant of a UAE article, with its own extraction state."""

    model_config = ConfigDict(frozen=True)

    language: Language
    article_url: str
    listing_url: str | None = None
    title: str
    published_at: date | None = None
    summary: str | None = None
    image_url: str | None = None
    error: str | None = None
    text: str | None = None
    content_hash: str | None = None
    extraction_status: ExtractionStatus = "pending"

    @field_validator("title")
    @classmethod
    def _require_title(cls, value: str) -> str:
        """### Reject a blank localization title."""
        if not value.strip():
            raise ValueError("title must not be blank")
        return value.strip()

    @field_validator("article_url")
    @classmethod
    def _require_article_url(cls, value: str) -> str:
        """### Reject a blank article URL."""
        if not value.strip():
            raise ValueError("article_url must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _require_content_when_complete(self) -> UAEArticleLocalization:
        """### A completed localization must carry marked text and its hash."""
        if self.extraction_status != "complete":
            return self
        text = self.text
        if text is None or not text.strip():
            raise ValueError("complete localization requires non-empty text")
        if not text.startswith(TEXT_BEGIN_MARKER):
            raise ValueError(f"complete localization text must start with {TEXT_BEGIN_MARKER}")
        if not (self.content_hash or "").strip():
            raise ValueError("complete localization requires a content hash")
        if self.content_hash != compute_content_hash(text):
            raise ValueError("content hash does not match the localization text")
        return self

    @property
    def article_key(self) -> str:
        """### Derive the language-independent article key from the URL slug."""
        return article_key_from_url(self.article_url)

    def to_translation(self) -> DocumentTranslation:
        """### Convert a completed localization into a canonical translation record."""
        if self.extraction_status != "complete" or self.text is None or self.content_hash is None:
            raise ValueError("only completed localizations can be converted")
        return DocumentTranslation(
            language=self.language,
            source_url=self.article_url,
            title=self.title,
            text=self.text,
            content_hash=self.content_hash,
            published_at=self.published_at,
            summary=self.summary,
            image_url=self.image_url,
            status="complete",
        )

    def to_canonical_state(self, *, parent_identity: str) -> DocumentTranslation:
        """### Preserve this localization's state in the canonical document."""
        completed = self.extraction_status == "complete"
        return DocumentTranslation(
            language=self.language,
            source_url=self.article_url,
            title=self.title,
            text=self.text if completed else None,
            content_hash=self.content_hash if completed else None,
            status=self.extraction_status,
            error=None if completed else self.error or f"localization extraction {self.extraction_status}",
            listing_url=self.listing_url,
            parent_identity=parent_identity,
            published_at=self.published_at,
            summary=self.summary,
            image_url=self.image_url,
        )


class UAEArticle(BaseModel):
    """### One logical UAE article with an Arabic primary and optional translations."""

    model_config = ConfigDict(frozen=True)

    source: SourceName = SOURCE_NAME
    article_key: str
    primary_language: Literal["ar"] = "ar"
    primary: UAEArticleLocalization
    translations: dict[str, UAEArticleLocalization] = Field(default_factory=dict)

    @field_validator("article_key")
    @classmethod
    def _require_article_key(cls, value: str) -> str:
        """### Reject a blank article key."""
        if not value.strip():
            raise ValueError("article_key must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def _check_languages(self) -> UAEArticle:
        """### Enforce the primary language and consistent translation entries."""
        if self.primary.language != PRIMARY_LANGUAGE:
            raise ValueError("primary localization must be Arabic")
        for language, localization in self.translations.items():
            if language not in SUPPORTED_LANGUAGES:
                raise ValueError(f"unsupported translation language {language!r}")
            if language == PRIMARY_LANGUAGE:
                raise ValueError("the primary language must not be repeated in translations")
            if localization.language != language:
                raise ValueError(f"translation {language!r} declares language {localization.language!r}")
        return self

    @property
    def completed_localizations(self) -> tuple[UAEArticleLocalization, ...]:
        """### Return every localization whose extraction completed."""
        candidates = (self.primary, *self.translations.values())
        return tuple(item for item in candidates if item.extraction_status == "complete")


def article_key_from_url(url: str) -> str:
    """### Derive the language-independent article key from an article URL."""
    path = urlparse(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1]


def translated_article_url(article_url: str, *, target_language: str) -> str:
    """### Swap the language segment of an article URL, keeping the shared slug."""
    parsed = urlparse(article_url)
    segments = parsed.path.split("/")
    for index, segment in enumerate(segments):
        if segment in SUPPORTED_LANGUAGES:
            segments[index] = target_language
            break
    return parsed._replace(path="/".join(segments)).geturl()


def validate_localization(localization: UAEArticleLocalization, *, expected_host: str) -> ValidationReport:
    """### Validate one localization's identity and content independently."""
    issues: list[ValidationIssue] = []
    for field, value, required in (
        ("article_url", localization.article_url, True),
        ("listing_url", localization.listing_url, False),
        ("image_url", localization.image_url, False),
    ):
        problem = http_url_issue(value, field=field, required=required)
        if problem is not None:
            issues.append(problem)

    problem = host_issue(localization.article_url, field="article_url", expected_host=expected_host)
    if problem is not None:
        issues.append(problem)
    if localization.image_url:
        problem = host_issue(localization.image_url, field="image_url", expected_host=expected_host)
        if problem is not None:
            issues.append(problem)
    if localization.article_key != article_key_from_url(localization.article_url):
        issues.append(
            ValidationIssue(
                field="article_url",
                code="article_key_mismatch",
                message="article key does not match the article URL",
            )
        )
    issues.extend(text_issues(localization.text, localization.content_hash, status=localization.extraction_status))
    return ValidationReport(issues=tuple(issues))


def validate_article(article: UAEArticle, *, expected_host: str) -> ValidationReport:
    """### Validate one assembled article without discarding its valid localizations."""
    issues: list[ValidationIssue] = []
    if article.primary.article_key != article.article_key:
        issues.append(
            ValidationIssue(
                field="primary.article_url",
                code="primary_key_mismatch",
                message="primary localization slug does not match the article key",
            )
        )
    for language, localization in article.translations.items():
        if localization.article_key != article.article_key:
            issues.append(
                ValidationIssue(
                    field=f"translations.{language}.article_url",
                    code="translation_key_mismatch",
                    message=f"{language} localization slug does not match the article key",
                )
            )

    issues.extend(validate_localization(article.primary, expected_host=expected_host).issues)
    for language, localization in article.translations.items():
        report = validate_localization(localization, expected_host=expected_host)
        # $ Prefix the findings so a bad localization is traceable without blocking the other.$
        issues.extend(
            issue.model_copy(update={"field": f"translations.{language}.{issue.field}"}) for issue in report.issues
        )

    completed = article.completed_localizations
    if not completed:
        issues.append(
            ValidationIssue(
                field="translations",
                code="no_completed_localization",
                message="no localization completed extraction",
            )
        )

    primary = article.primary
    for language, localization in article.translations.items():
        if localization.extraction_status != "complete":
            continue
        if primary.extraction_status == "complete":
            if localization.published_at != primary.published_at:
                issues.append(
                    ValidationIssue(
                        field=f"translations.{language}.published_at",
                        code="date_mismatch",
                        message="localizations report different publication dates",
                        severity="warning",
                    )
                )
            if localization.image_url != primary.image_url:
                issues.append(
                    ValidationIssue(
                        field=f"translations.{language}.image_url",
                        code="image_mismatch",
                        message="localizations report different image URLs",
                        severity="warning",
                    )
                )
    return ValidationReport(issues=tuple(issues))


def validate_listing_entry(
    *,
    title: str,
    article_url: str,
    listing_url: str,
    language: str,
    expected_host: str,
) -> ValidationReport:
    """### Validate one discovered listing record before enrichment."""
    issues: list[ValidationIssue] = []
    if not title.strip():
        issues.append(ValidationIssue(field="title", code="title_missing", message="listing title is blank"))
    if language not in SUPPORTED_LANGUAGES:
        issues.append(
            ValidationIssue(field="language", code="language_unsupported", message=f"unsupported language {language!r}")
        )
    for field, value in (("article_url", article_url), ("listing_url", listing_url)):
        problem = http_url_issue(value, field=field)
        if problem is not None:
            issues.append(problem)
        else:
            problem = host_issue(value, field=field, expected_host=expected_host)
            if problem is not None:
                issues.append(problem)
    if http_url_issue(article_url, field="article_url") is None:
        segments = urlparse(article_url).path.split("/")
        declared = [segment for segment in segments if segment in SUPPORTED_LANGUAGES]
        if declared and declared[0] != language:
            issues.append(
                ValidationIssue(
                    field="article_url",
                    code="language_prefix_mismatch",
                    message=f"URL declares {declared[0]!r} but the record declares {language!r}",
                )
            )
    return ValidationReport(issues=tuple(issues))


def to_canonical(article: UAEArticle, *, include_unconfigured_english: bool = False) -> CanonicalDocument | None:
    """### Adapt one validated article to the canonical contract, or None without content."""
    completed = article.completed_localizations
    if not completed:
        return None
    # $ Prefer the Arabic primary; fall back to a completed translation when Arabic is blocked.$
    chosen = article.primary if article.primary.extraction_status == "complete" else completed[0]
    if chosen.extraction_status != "complete" or chosen.text is None or chosen.content_hash is None:
        return None
    identity = f"{article.source}:id:{article.article_key}"
    localizations = [article.primary, *article.translations.values()]
    others = [
        item.to_canonical_state(parent_identity=identity) for item in localizations if item.language != chosen.language
    ]
    if include_unconfigured_english and not any(item.language == "en" for item in localizations):
        others.append(DocumentTranslation(language="en", status="not_configured", parent_identity=identity))
    return CanonicalDocument(
        source=article.source,
        native_id=article.article_key,
        source_url=chosen.article_url,
        listing_url=chosen.listing_url,
        title=chosen.title,
        language=chosen.language,
        extraction_status="complete",
        text=chosen.text,
        content_hash=chosen.content_hash,
        published_at=chosen.published_at,
        summary=chosen.summary,
        image_url=chosen.image_url,
        translations=tuple(others),
    )


def metadata_document(
    article: UAEArticle,
    *,
    preferred_language: str = PRIMARY_LANGUAGE,
) -> CanonicalDocument:
    """### Store discovery metadata for an article whose localizations did not complete."""
    localization = (
        article.primary
        if preferred_language == PRIMARY_LANGUAGE
        else article.translations.get(preferred_language, article.primary)
    )
    identity = f"{article.source}:id:{article.article_key}"
    localizations = [article.primary, *article.translations.values()]
    translation_states = [item.to_canonical_state(parent_identity=identity) for item in localizations]
    if len(localizations) == 1:
        translation_states.append(DocumentTranslation(language="en", status="not_configured", parent_identity=identity))
    return CanonicalDocument(
        source=article.source,
        native_id=article.article_key,
        source_url=localization.article_url,
        listing_url=localization.listing_url,
        title=localization.title,
        language=localization.language,
        extraction_status=localization.extraction_status,
        extraction_error=localization.error,
        published_at=localization.published_at,
        summary=localization.summary,
        image_url=localization.image_url,
        translations=tuple(translation_states),
    )


__all__ = [
    "PRIMARY_LANGUAGE",
    "SOURCE_NAME",
    "SUPPORTED_LANGUAGES",
    "ExtractionStatus",
    "Language",
    "UAEArticle",
    "UAEArticleLocalization",
    "article_key_from_url",
    "metadata_document",
    "to_canonical",
    "translated_article_url",
    "validate_article",
    "validate_listing_entry",
    "validate_localization",
]
