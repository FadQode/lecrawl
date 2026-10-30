"""### Validation contracts and rules for LexCrawl sources."""

from __future__ import annotations

from lexcrawl.validation.base import (
    IssueSeverity,
    ValidationIssue,
    ValidationReport,
    host_issue,
    http_url_issue,
    text_issues,
)
from lexcrawl.validation.cba import (
    DOCUMENT_TYPE,
    RESOURCE_TYPES,
    CbaNewsRelease,
    CbaReleaseValidation,
    CbaResource,
    display_titles,
    filename_from_url,
    normalize_resource_type,
    normalize_topics,
)
from lexcrawl.validation.cba import to_canonical as cba_to_canonical
from lexcrawl.validation.cba import validate_release as validate_cba_release
from lexcrawl.validation.cba import validate_resource as validate_cba_resource
from lexcrawl.validation.uae import (
    PRIMARY_LANGUAGE,
    SOURCE_NAME,
    SUPPORTED_LANGUAGES,
    ExtractionStatus,
    Language,
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

__all__ = [
    "DOCUMENT_TYPE",
    "PRIMARY_LANGUAGE",
    "RESOURCE_TYPES",
    "SOURCE_NAME",
    "SUPPORTED_LANGUAGES",
    "CbaNewsRelease",
    "CbaReleaseValidation",
    "CbaResource",
    "ExtractionStatus",
    "IssueSeverity",
    "Language",
    "UAEArticle",
    "UAEArticleLocalization",
    "ValidationIssue",
    "ValidationReport",
    "article_key_from_url",
    "cba_to_canonical",
    "display_titles",
    "filename_from_url",
    "host_issue",
    "http_url_issue",
    "metadata_document",
    "normalize_resource_type",
    "normalize_topics",
    "text_issues",
    "to_canonical",
    "translated_article_url",
    "validate_article",
    "validate_cba_release",
    "validate_cba_resource",
    "validate_listing_entry",
    "validate_localization",
]
