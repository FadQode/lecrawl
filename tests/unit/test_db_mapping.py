"""### Unit tests for the canonical-to-column mapping (no database required)."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from lexcrawl.actors.base import ActorRunResult
from lexcrawl.models.document import compute_content_hash
from lexcrawl.repositories.mapping import (
    DOCUMENT_ERROR_TYPE,
    EXTRACTION_STAGE,
    RESOURCE_ERROR_TYPE,
    RUN_ERROR_STAGE,
    RUN_ERROR_TYPE,
    classify_run_error,
    document_status,
    document_type,
    document_values,
    error_summary,
    localization_payloads,
    published_precision,
    redact_secrets,
    resource_values,
    run_counters,
    run_error_payloads,
)
from tests.integration.db.builders import document, resource, translation

NOW = datetime(2026, 11, 3, 12, 0, 0)


def test_precision_prefers_the_stated_value() -> None:
    """### Explicit precision is kept as declared."""
    assert (
        published_precision(
            document(published_precision="month", published_month=1, published_year=2025, published_at=None)
        )
        == "month"
    )


def test_precision_infers_day_from_an_exact_date() -> None:
    """### An exact date without a stated precision is day precision."""
    assert published_precision(document(published_at=date(2026, 1, 8))) == "day"


def test_precision_is_unknown_without_dates() -> None:
    """### No date and no precision stays unknown rather than invented."""
    assert published_precision(document(published_at=None)) == "unknown"


def test_document_type_prefers_the_source_label() -> None:
    """### A source classification wins over the per-source default."""
    assert document_type(document(source="cba_news_releases", type_of_law="News Releases")) == "News Releases"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("uae_legislation_news", "news_article"),
        ("cba_news_releases", "news_release"),
        ("commerce_reports", "report"),
        ("another_source", "document"),
    ],
)
def test_document_type_defaults_per_source(source: str, expected: str) -> None:
    """### Sources without a label fall back to their default classification."""
    url = f"https://listing.example.test/{source}/one"
    assert document_type(document(source=source, url=url, published_at=None)) == expected


def test_document_status_uses_the_contract_then_content() -> None:
    """### The explicit status wins, then content decides, then pending."""
    assert document_status(document(status="access_blocked", text=None)) == "access_blocked"
    assert document_status(document(status=None, text="body")) == "complete"
    assert document_status(document(status=None, text=None)) == "pending"


def test_document_values_carry_every_persisted_field() -> None:
    """### The document mapping exposes all canonical metadata."""
    values = document_values(
        document(topics=("#a", "#b"), url="https://listing.example.test/ar/news/one"),
        now=NOW,
    )
    assert values["identity_key"] == "uae_legislation_news:url:https://listing.example.test/ar/news/one"
    assert values["topics"] == ["#a", "#b"]
    assert values["provenance"] == "live"
    assert values["published_precision"] == "day"
    assert values["first_seen_at"] == NOW == values["last_seen_at"]
    assert values["extraction_attempts"] == 0


def test_incomplete_documents_start_with_one_attempt() -> None:
    """### An incomplete document starts the bounded retry counter at one."""
    assert document_values(document(status="failed", text=None, published_at=None), now=NOW)["extraction_attempts"] == 1


def test_localization_payloads_lead_with_the_primary() -> None:
    """### The primary localization comes first and translations keep their status."""
    canonical = document(
        text="نص",
        translations=(
            translation(language="en"),
            translation(
                language="fr",
                url="https://listing.example.test/fr/news/one",
                status="access_blocked",
                text=None,
                error="blocked",
            ),
        ),
    )
    payloads = localization_payloads(canonical)
    assert [payload.language_code for payload in payloads] == ["ar", "en", "fr"]
    assert payloads[0].status == "complete"
    assert payloads[2].status == "access_blocked"
    assert payloads[2].text_content is None


def test_translated_content_is_marked_and_hashed() -> None:
    """### Translation text keeps the marker and its hash."""
    payloads = localization_payloads(document(translations=(translation(),)))
    english = payloads[1]
    assert english.text_content is not None and english.text_content.startswith("TEXT-BEGIN")
    assert english.content_hash == compute_content_hash(english.text_content)


def test_resource_values_require_identity_and_map_fields() -> None:
    """### Resources map their metadata and refuse unkeyed rows."""
    attached = document(resources=(resource(file_size=2048),)).resources[0]
    values = resource_values(attached, now=NOW)
    assert values["identity_key"] == attached.identity_key
    assert values["resource_type"] == "pdf"
    assert values["file_size_bytes"] == 2048

    with pytest.raises(ValueError, match="identity key"):
        resource_values(resource(), now=NOW)


def test_run_counters_use_documented_units() -> None:
    """### Processed documents count rows; failures mirror extraction failures."""
    canonical = document()
    counters = run_counters(
        ActorRunResult(
            source="uae_legislation_news",
            documents=[canonical],
            listing_pages_attempted=2,
            detail_pages_attempted=3,
            detail_pages_failed=1,
            resources_processed=2,
            resources_failed=1,
        )
    )
    assert counters["documents_processed"] == 1
    assert counters["documents_failed"] == 1
    assert counters["listing_pages_attempted"] == 2
    assert counters["detail_pages_failed"] == 1
    assert counters["resources_processed"] == 2
    assert counters["resources_failed"] == 1


def test_run_error_payloads_are_redacted_and_bounded() -> None:
    """### Run errors are classified, redacted, and bounded."""
    result = ActorRunResult(
        source="commerce_reports",
        errors=("article fetch failed at https://x.test/a?api_key=SECRET", "x" * 5000),
    )
    payloads = run_error_payloads(result)
    assert [payload.error_type for payload in payloads] == ["fetch_error", RUN_ERROR_TYPE]
    assert [payload.stage for payload in payloads] == ["fetch", RUN_ERROR_STAGE]
    assert "SECRET" not in payloads[0].message
    assert len(payloads[1].message) <= 2000
    assert run_error_payloads(ActorRunResult(source="s")) == []


@pytest.mark.parametrize(
    ("message", "stage", "error_type"),
    [
        ("article fetch failed at https://x/y: boom", "fetch", "fetch_error"),
        ("listing fetch failed at https://x/y: boom", "fetch", "fetch_error"),
        ("listing parse failed: boom", "parse", "parse_error"),
        ("listing page 2 did not contain the report table", "parse", "parse_error"),
        ("no article URLs discovered on the listing pages", "discovery", "discovery_error"),
        ("listing range gap before page 2 (20 -> 45)", "discovery", "discovery_error"),
        ("stopped after hitting max_pages=25", "discovery", "budget_error"),
        ("resource access_blocked at https://x/a.pdf: blocked", "extract", "resource_error"),
        ("pdf processing failed at https://x/a.pdf: boom", "extract", "resource_error"),
        ("change detection requires persisted documents", "persistence", "persistence_error"),
        ("unexpected actor phase failure: boom", "unknown", "actor_error"),
        ("a message with no recognized phrasing", "unknown", "actor_error"),
    ],
)
def test_classify_run_error_maps_known_phrasing(message: str, stage: str, error_type: str) -> None:
    """### Known actor phrasing maps to the existing stage vocabulary; the rest stays generic."""
    assert classify_run_error(message) == (stage, error_type)


def test_error_summary_is_bounded_and_redacted() -> None:
    """### The stored summary never carries secrets and stays small."""
    assert error_summary(ActorRunResult(source="s")) is None
    summary = error_summary(
        ActorRunResult(source="s", errors=("postgresql+psycopg://user:password@host/db", "b" * 5000))
    )
    assert summary is not None
    assert "password" not in summary
    assert len(summary) <= 4000


def test_redact_secrets_covers_dsn_keys_and_tokens() -> None:
    """### Credentials, API keys, and tokens are masked."""
    redacted = redact_secrets("postgresql://u:p@h/db https://x/a?api_key=K&token=T")
    assert "u:p@" not in redacted
    assert "api_key=***" in redacted
    assert "token=***" in redacted


def test_error_metadata_constants_are_stable() -> None:
    """### Error stage and type vocabulary is explicit."""
    assert EXTRACTION_STAGE == "extract"
    assert (DOCUMENT_ERROR_TYPE, RESOURCE_ERROR_TYPE) == (
        "document_extraction_error",
        "resource_extraction_error",
    )


def test_run_counters_include_budget_accounting() -> None:
    """### Pending work and provider credits are persisted counters."""
    counters = run_counters(ActorRunResult(source="commerce_reports", documents_pending=3, provider_credits_used=5))
    assert counters["documents_pending"] == 3
    assert counters["provider_credits_used"] == 5


def test_run_status_treats_no_change_as_success() -> None:
    """### A verified no-change run succeeds; a failed check does not."""
    assert ActorRunResult(source="s", check_outcome="no_change").status == "success"
    assert ActorRunResult(source="s", check_outcome="check_failed", errors=["boom"]).status == "failed"
    assert ActorRunResult(source="s", check_outcome="check_failed").status == "failed"
    assert ActorRunResult(source="s", check_outcome="new_data").status == "failed"
