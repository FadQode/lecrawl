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
from lexcrawl.validation.commerce import (
    MONTH_PRECISION,
    CommerceReport,
    CommerceResource,
    CommerceValidation,
    ListingDate,
    archive_timestamp,
    archive_url_for,
    from_api_payload,
    from_listing_row,
    parse_listing_date,
)
from lexcrawl.validation.commerce import to_canonical as commerce_to_canonical
from lexcrawl.validation.commerce import validate_report as validate_commerce_report
from lexcrawl.validation.commerce import validate_resource as validate_commerce_resource
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
    "MONTH_PRECISION",
    "PRIMARY_LANGUAGE",
    "RESOURCE_TYPES",
    "SOURCE_NAME",
    "SUPPORTED_LANGUAGES",
    "CbaNewsRelease",
    "CbaReleaseValidation",
    "CbaResource",
    "CommerceReport",
    "CommerceResource",
    "CommerceValidation",
    "ExtractionStatus",
    "IssueSeverity",
    "Language",
    "ListingDate",
    "UAEArticle",
    "UAEArticleLocalization",
    "ValidationIssue",
    "ValidationReport",
    "archive_timestamp",
    "archive_url_for",
    "article_key_from_url",
    "cba_to_canonical",
    "commerce_to_canonical",
    "display_titles",
    "filename_from_url",
    "from_api_payload",
    "from_listing_row",
    "host_issue",
    "http_url_issue",
    "metadata_document",
    "normalize_resource_type",
    "normalize_topics",
    "parse_listing_date",
    "text_issues",
    "to_canonical",
    "translated_article_url",
    "validate_article",
    "validate_cba_release",
    "validate_cba_resource",
    "validate_commerce_report",
    "validate_commerce_resource",
    "validate_listing_entry",
    "validate_localization",
]
